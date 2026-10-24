"""Round 23: screenshots of Settings → «Быстрый доступ» (rebuilt on the settings kit) against YOUR dev server.

    PYTHONUTF8=1 NO_PROXY='*' /workspace/work/venv/bin/python tests/quick_ask_settings_r23_shots.py http://127.0.0.1:PORT OUT_DIR [--quick]

Uses a private data copy (changes its preferences through /api/pg/preferences — never run it against the shared
dev server). Only clicks/keyboard on the page (CSP has no 'unsafe-eval').

Shots: the page in classic/glass/grid/focus/aurora × dark/light (viewport), every section separately (the settings
body scrolls, so a pane screenshot would be clipped), «Умная модель и картинки» and «Ещё» collapsed + expanded,
the own-endpoint form (empty, after a failed test), the sub-pages Команды / Голос / Компаньон, English.
Checks: one close click closes Settings, «Свой эндпоинт» reveals the endpoint fields, the sub-pages open and are not
empty, no horizontal overflow inside the pane, no page errors. ``--quick`` = only dark+light classic.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request
from pathlib import Path

CHROME = "/usr/bin/chromium" if os.path.exists("/usr/bin/chromium") else "/workspace/work/chromium/chrome"
SECTIONS = ("quick-basics", "quick-behaviour", "quick-extensions", "quick-advanced")


def set_prefs(base: str, patch: dict) -> None:
    request = urllib.request.Request(base + "/api/pg/preferences", data=json.dumps(patch).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(request, timeout=10).read()


async def open_quick(browser, base: str, problems: list, viewport=(1280, 900)):
    page = await browser.new_page(viewport={"width": viewport[0], "height": viewport[1]}, device_scale_factor=1)
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    await page.goto(base + "/", wait_until="networkidle")
    await page.wait_for_timeout(900)
    await page.keyboard.press("Control+Comma")
    await page.wait_for_timeout(700)
    await page.click('[data-set-tab="quick"]')
    await page.wait_for_timeout(1300)
    return page


async def overflow_check(page, problems: list, label: str) -> None:
    wide = await page.evaluate("""() => {
        const pane = document.querySelector('#pane-quick'); if (!pane) return [];
        const right = pane.getBoundingClientRect().right + 1;
        return [...pane.querySelectorAll('*')].filter((el) => el.offsetParent && el.getBoundingClientRect().right > right
            && !el.closest('.sx-help-pop, .sx-section-head') && !el.classList.contains('pretty-native')).slice(0, 5).map((el) => (el.id || el.className || el.tagName) + '');
    }""")
    if wide:
        problems.append(f"{label}: elements stick out of the pane: {wide}")


async def snap(page, path: Path, problems: list) -> None:
    """Viewport screenshot; headless WebGL (the companion preview) sometimes fails a capture — retry once."""
    for attempt in range(3):
        try:
            await page.screenshot(path=str(path))
            return
        except Exception as exc:  # noqa: BLE001
            if attempt == 2:
                print(f"note: could not capture {path.name}: {exc}".splitlines()[0])
            await page.wait_for_timeout(700)


async def section_shots(page, out: Path, suffix: str) -> None:
    for sid in SECTIONS:
        loc = page.locator(f"#{sid}")
        if await loc.count() and await loc.is_visible():
            await loc.scroll_into_view_if_needed()
            await page.wait_for_timeout(150)
            await loc.screenshot(path=str(out / f"sec-{sid}-{suffix}.png"))


async def main(base: str, out: Path, quick: bool) -> int:
    from playwright.async_api import async_playwright
    out.mkdir(parents=True, exist_ok=True)
    problems: list = []
    reset = {"quick_ask_source": "proxy", "quick_ask_endpoint_url": "", "quick_ask_endpoint_model": "", "profile_onboarded": True,
             "quick_ask_personality": "", "quick_ask_instructions": "", "quick_ask_name": "", "language": "ru"}
    combos = [("dark", "classic"), ("light", "classic")] if quick else \
        [("dark", "classic"), ("light", "classic"), ("dark", "glass"), ("light", "glass"), ("light", "grid"), ("dark", "focus"), ("light", "aurora")]
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        for theme, design in combos:
            set_prefs(base, dict(reset, theme=theme, ui_design=design))
            page = await open_quick(browser, base, problems)
            await page.screenshot(path=str(out / f"settings-{design}-{theme}.png"))
            await overflow_check(page, problems, f"{design}-{theme}")
            if design == "classic":
                await section_shots(page, out, f"{theme}")
                for more in ("quick-models-more", "quick-advanced-more"):
                    if await page.locator(f"#{more} > summary").count():
                        await page.locator(f"#{more} > summary").scroll_into_view_if_needed()
                        await page.click(f"#{more} > summary")
                        await page.wait_for_timeout(300)
                await section_shots(page, out, f"{theme}-open")
                await overflow_check(page, problems, f"{design}-{theme} expanded")
            if (theme, design) == ("dark", "classic"):
                await page.locator("#quick-basics").scroll_into_view_if_needed()
                await page.click('#qs-settings label:has(input[name="qa-source"][value="custom"])')
                await page.wait_for_timeout(700)
                if not await page.locator("#st-qa-ep-url").is_visible():
                    problems.append("«Свой эндпоинт» did not reveal the address field")
                await page.locator("#quick-basics").screenshot(path=str(out / "endpoint-empty-dark.png"))
                await page.fill("#st-qa-ep-url", "http://127.0.0.1:9/v1")
                await page.click("#st-qa-ep-test")
                await page.wait_for_timeout(2500)
                result = await page.locator("#qs-ep-result").inner_text()
                print("test result:", result)
                if not result.strip():
                    problems.append("the connection test showed nothing")
                await page.locator("#quick-basics").screenshot(path=str(out / "endpoint-failed-dark.png"))
                await overflow_check(page, problems, "endpoint form")
                await page.fill("#st-qa-ep-url", "")
                await page.wait_for_timeout(600)
                await page.click('#qs-settings label:has(input[name="qa-source"][value="proxy"])')
                await page.wait_for_timeout(700)
                for sub, name in (("quick-commands", "commands"), ("quick-voice", "voice"), ("quick-companion", "companion")):
                    await page.locator(f'[data-sx-open="{sub}"]').scroll_into_view_if_needed()
                    await page.click(f'[data-sx-open="{sub}"]')
                    await page.wait_for_timeout(1500)
                    box = page.locator(f'[data-sx-sub="{sub}"]')
                    if not await box.is_visible():
                        problems.append(f"sub-page {sub} did not open")
                    else:
                        text = await box.inner_text()
                        if len(text.strip()) < 40:
                            problems.append(f"sub-page {sub} looks empty")
                        await snap(page, out / f"sub-{name}-1.png", problems)
                        await page.mouse.move(800, 600)
                        opener = box.locator("[data-mcs-more] > summary, [data-companion-more] > summary")
                        if await opener.count():
                            await opener.first.click()
                            await page.wait_for_timeout(400)
                        for i in (2, 3):
                            await page.mouse.wheel(0, 700)
                            await page.wait_for_timeout(300)
                            await snap(page, out / f"sub-{name}-{i}.png", problems)
                        await overflow_check(page, problems, f"sub {sub}")
                    back = page.locator(f'[data-sx-sub="{sub}"] [data-sx-back], [data-sx-back]')
                    if await back.count() and await back.first.is_visible():
                        await back.first.click()
                        await page.wait_for_timeout(500)
                    else:
                        await page.keyboard.press("Escape")
                        await page.wait_for_timeout(400)
                close = page.locator("#panel .panel-close")
                if await close.count() and await close.first.is_visible():
                    await close.first.click()
                    await page.wait_for_timeout(500)
                    if await page.locator("#panel-back.visible").count():
                        problems.append("the settings dialog did not close with one click (stacked)")
            await page.close()
        set_prefs(base, dict(reset, theme="light", ui_design="classic", language="en"))
        page = await open_quick(browser, base, problems)
        await page.screenshot(path=str(out / "settings-classic-light-en.png"))
        await overflow_check(page, problems, "english")
        await page.close()
        set_prefs(base, dict(reset, theme="dark", ui_design="classic"))
        await browser.close()
    for line in problems:
        print(line)
    return 1 if problems else 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    raise SystemExit(asyncio.run(main(args[0].rstrip("/"), Path(args[1]), "--quick" in sys.argv)))
