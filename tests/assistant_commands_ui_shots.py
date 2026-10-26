"""Screenshots of the Quick Ask «Команды» plugin (Round 22).

Usage: python tests/assistant_commands_ui_shots.py http://127.0.0.1:PORT OUT_DIR

Needs a running MemuAI dev server (devrun.py) with the assistant routes. Commands that
need Windows (volume, layouts, images) are answered with fixture cards through
Playwright routing, everything else hits the real backend (timers, help, confirmation).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from playwright.async_api import async_playwright

CHROME = "/usr/bin/chromium" if os.path.exists("/usr/bin/chromium") else "/workspace/work/chromium/chrome"

FIXTURES = {
    "громкость 30": {"id": "fx1", "action": "volume.set", "title": "Изменить громкость", "status": "done", "message": "Громкость 30%",
                     "kind": "level", "data": {"level": 30}, "undo": "u1", "tier": "safe"},
    "доброе утро": {"id": "fx2", "action": "command.run", "title": "Запустить свою команду", "status": "done", "kind": "routine",
                    "message": "Доброе утро — готово", "tier": "safe", "steps": [
                        {"status": "done", "title": "Изменить громкость", "message": "Громкость 35%"},
                        {"status": "done", "title": "Тёмная/светлая тема Windows", "message": "Светлая тема Windows"},
                        {"status": "done", "title": "Погода", "message": "Сейчас +9° (ощущается как +6°), пасмурно"},
                        {"status": "done", "title": "Раскладка окон", "message": "окон на месте: 4 из 4"},
                        {"status": "skipped", "title": "Сказать", "message": "условие не выполнено"}]},
    "закажи роллы": {"id": "fx3", "action": "food.order", "title": "Заказать еду", "status": "done", "tier": "safe",
                     "message": "Открыл поиск «роллы» в Яндекс Еде. Заказ и оплату вы оформляете сами — MemuAI не заказывает и не платит за вас.",
                     "links": [{"label": "https://eda.yandex.ru/search?query=роллы", "url": "https://eda.yandex.ru/search?query=%D1%80%D0%BE%D0%BB%D0%BB%D1%8B"}]},
    "сколько заряда": {"id": "fx4", "action": "sysinfo.get", "title": "Сведения о системе", "status": "done", "tier": "safe", "kind": "sysinfo",
                       "message": "Сведения о системе", "details": [["Процессор", "14% · AMD Ryzen 7 7840HS"], ["Память", "61% · 19.4 ГБ / 31.7 ГБ"],
                                                                    ["Батарея", "81%, осталось ~2 ч 10 мин"], ["Диск C:", "свободно 212.4 ГБ из 951.6 ГБ"]]},
}

async def main(base: str, out: str) -> None:
    os.makedirs(out, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])

        async def page_for(design="classic", theme="dark", w=900, h=760):
            page = await browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))

            async def fixture(route):
                try:
                    body = json.loads(route.request.post_data or "{}")
                except ValueError:
                    body = {}
                text = str(body.get("text") or "").strip().lower()
                if text in FIXTURES:
                    await route.fulfill(content_type="application/json", body=json.dumps({"card": FIXTURES[text], "match": {}}, ensure_ascii=False))
                else:
                    await route.continue_()
            await page.route("**/api/assistant/run", fixture)
            await page.add_init_script(f"try{{localStorage.setItem('memu_design','{design}');localStorage.setItem('memu_theme','{theme}')}}catch(e){{}}")
            await page.goto(base + "/quick/", wait_until="networkidle")
            await page.wait_for_timeout(900)
            await page.evaluate(f"document.documentElement.dataset.design='{design}';document.documentElement.dataset.theme='{theme}'")
            return page, errors

        async def say(page, text, wait=700):
            await page.fill("#qa-input", text)
            await page.press("#qa-input", "Enter")
            await page.wait_for_timeout(wait)

        page, errors = await page_for()
        await page.request.post(base + "/api/assistant/timers", data={"op": "stopwatch", "action": "reset"})
        await say(page, "поставь таймер на 25 минут для чая")
        await say(page, "запусти помодоро")
        await say(page, "громкость 30")
        await page.wait_for_timeout(1200)
        await page.screenshot(path=f"{out}/01-timer-pomodoro-volume.png")
        await say(page, "что ты умеешь", 900)
        await page.screenshot(path=f"{out}/02-help.png")

        await say(page, "выключи компьютер", 900)
        await page.screenshot(path=f"{out}/03-confirm-shutdown.png")
        button = page.locator('[data-mqc="confirm"]').first
        if await button.count():
            await button.click()
            await page.wait_for_timeout(1300)
            await page.screenshot(path=f"{out}/04-countdown.png")
            abort = page.locator('[data-mqc="abort"]').first
            if await abort.count():
                await abort.click()
                await page.wait_for_timeout(600)

        await say(page, "доброе утро")
        await say(page, "закажи роллы")
        await page.screenshot(path=f"{out}/05-routine-order.png")
        await say(page, "сколько заряда")
        await page.screenshot(path=f"{out}/06-sysinfo.png")
        print("errors (classic):", errors[:5])

        await page.request.post(base + "/api/assistant/timers", data={"op": "add", "seconds": 1, "label": "пицца"})
        await page.wait_for_timeout(3600)
        await page.screenshot(path=f"{out}/07-ringing.png")
        for ring in (await (await page.request.get(base + "/api/assistant/state")).json())["timers"]["ringing"]:
            await page.request.post(base + "/api/assistant/timers", data={"op": "dismiss", "id": ring["id"]})
        await page.close()

        for design, theme in (("glass", "dark"), ("aurora", "light")):
            page, errors = await page_for(design, theme)
            await say(page, "громкость 30")
            await say(page, "выключи компьютер", 900)
            await page.screenshot(path=f"{out}/08-{design}-{theme}.png")
            print(f"errors ({design}):", errors[:5])
            await page.close()

        for theme in ("dark", "light"):
            page = await browser.new_page(viewport={"width": 980, "height": 1500}, device_scale_factor=1)
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(base + "/quick/", wait_until="networkidle")
            await page.evaluate("""(theme) => {
              document.documentElement.dataset.theme = theme;
              document.body.innerHTML = '<div id="host" style="max-width:900px;margin:20px auto;background:var(--bg);padding:16px;border-radius:16px"></div>';
              document.body.style.overflow = 'auto';
              document.body.style.background = 'var(--bg)';
              const l = document.createElement('link'); l.rel='stylesheet'; l.href='/quick/plugins/commands-settings.css'; document.head.appendChild(l);
              const s = document.createElement('script'); s.src='/quick/plugins/commands-settings.js';
              s.onload = () => window.MemuCommandsSettings.mount(document.getElementById('host'), {en: false});
              document.body.appendChild(s);
            }""", theme)
            await page.wait_for_timeout(1500)
            await page.screenshot(path=f"{out}/09-settings-{theme}.png", full_page=True)
            await page.click('[data-act="template"]')
            await page.wait_for_timeout(500)
            await page.screenshot(path=f"{out}/10-editor-{theme}.png", full_page=True)
            await page.click('[data-act="toggle-actions"]') if await page.locator('[data-act="toggle-actions"]').count() else None
            print(f"errors (settings {theme}):", errors[:5])
            await page.close()
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main(sys.argv[1].rstrip("/"), sys.argv[2]))
