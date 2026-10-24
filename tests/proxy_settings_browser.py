"""Browser checks of the Round 23 Proxy Settings page: navigation, search, controls, theme, speed.

Usage: python tests/proxy_settings_browser.py http://127.0.0.1:PORT [OUT_DIR]

Needs a running Proxy on a DISPOSABLE data copy (it flips `strict_models` and `host` and restores
their defaults through the page's own "restore default" buttons). Polls from Python: the panel CSP
has no 'unsafe-eval'. Exits with code 1 and prints the failed checks when anything is wrong;
OUT_DIR (optional) receives screenshots of the main states."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request

from playwright.async_api import async_playwright

CHROME = "/usr/bin/chromium" if os.path.exists("/usr/bin/chromium") else "/workspace/work/chromium/chrome"


async def wait_for(page, expression: str, timeout: float = 15.0) -> None:
    for _ in range(int(timeout * 10)):
        if await page.evaluate(expression):
            return
        await page.wait_for_timeout(100)
    raise TimeoutError(expression)


def get_settings(base: str) -> dict:
    with urllib.request.urlopen(base + "/api/admin/settings", timeout=10) as response:
        return json.load(response)["settings"]


async def main(base: str, out: str | None) -> int:
    failures: list[str] = []
    report: dict = {}

    def check(name: str, ok: bool, detail="") -> None:
        report[name] = "ok" if ok else f"FAIL {detail}"
        if not ok:
            failures.append(f"{name}: {detail}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        await page.add_init_script("if (!sessionStorage.getItem('sg_test')) { sessionStorage.setItem('sg_test', '1'); "
                                   "localStorage.setItem('oh_lang', 'ru'); localStorage.removeItem('oh_settings_sec'); }")
        await page.goto(base + "/", wait_until="networkidle")
        await page.click('[data-tab="settings"]')
        await wait_for(page, "document.querySelectorAll('#models-table tr[data-mid]').length > 100")
        await wait_for(page, "!!document.querySelector('#provider-settings .sg-group')")

        visible = "Array.from(document.querySelectorAll('#sg-body .sg-sec')).filter(s => !s.hidden).map(s => s.dataset.sec)"
        check("opens_on_general", await page.evaluate(visible) == ["general"], await page.evaluate(visible))

        # Navigation: one section at a time, the choice survives a reload.
        await page.click('[data-sg-nav="reliability"]')
        check("nav_switches", await page.evaluate(visible) == ["reliability"], await page.evaluate(visible))
        check("nav_aria_current", await page.evaluate(
            "document.querySelector('[data-sg-nav=\"reliability\"]').getAttribute('aria-current') === 'page'"))
        await page.reload(wait_until="networkidle")
        await page.click('[data-tab="settings"]')
        await page.wait_for_timeout(300)
        check("nav_remembered", await page.evaluate(visible) == ["reliability"], await page.evaluate(visible))
        await wait_for(page, "!!document.querySelector('#provider-settings .sg-group')")

        # The model catalog folds the rail and still renders 600+ rows fast.
        await page.click('[data-sg-nav="models"]')
        await wait_for(page, "document.querySelectorAll('#models-table tr[data-mid]').length > 100")
        check("models_rail_folds", await page.evaluate("document.getElementById('sg-shell').classList.contains('is-wide')"))
        timing = await page.evaluate("""() => { const t0 = performance.now(); renderModelsTable();
          return { ms: +(performance.now() - t0).toFixed(1), total: modelsCache.length }; }""")
        report["models_first_paint"] = timing
        check("models_fast", timing["ms"] < 120, timing)
        await page.wait_for_timeout(800)
        rows = await page.evaluate("document.querySelectorAll('#models-table tr[data-mid]').length")
        check("models_all_rows", rows == timing["total"], (rows, timing["total"]))
        if out:
            await page.screenshot(path=f"{out}/browser-models.png")

        # Search: "/" focuses, matches open «Дополнительно», the rail counts hits, Esc restores.
        await page.click('[data-sg-nav="general"]')
        await page.keyboard.press("/")
        check("slash_focuses_search", await page.evaluate("document.activeElement.id === 'sg-search'"))
        await page.keyboard.type("таймаут")
        await page.wait_for_timeout(250)
        shown = await page.evaluate(visible)
        check("search_finds_timeouts", "reliability" in shown and "providers" in shown, shown)
        check("search_opens_advanced", await page.evaluate(
            "document.querySelector('#sg-sec-reliability details.sg-more').open"))
        check("search_hides_misses", await page.evaluate(
            "getComputedStyle(document.querySelector('#set-passes').closest('.sg-row')).display === 'none'"))
        hits = await page.evaluate("document.querySelector('[data-sg-nav=\"reliability\"]').dataset.hits")
        check("rail_counts_hits", hits == "3", hits)
        if out:
            await page.screenshot(path=f"{out}/browser-search.png")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(150)
        check("escape_clears", await page.evaluate(visible) == ["general"], await page.evaluate(visible))
        check("advanced_closed_again", not await page.evaluate(
            "document.querySelector('#sg-sec-reliability details.sg-more').open"))

        # Cross-language keywords and the "elsewhere" list.
        await page.fill("#sg-search", "tunnel")
        await page.wait_for_timeout(200)
        found = await page.evaluate("Array.from(document.querySelectorAll('#sg-found-list b')).map(b => b.textContent)")
        check("elsewhere_public", "Публичный доступ" in found, found)
        # A model name leads to the catalog with the filter applied.
        await page.fill("#sg-search", "llama")
        await page.wait_for_timeout(200)
        has_models = await page.evaluate("!!document.querySelector('[data-sg-models]')")
        check("model_name_found", has_models)
        if has_models:
            await page.click("[data-sg-models]")
            await page.wait_for_timeout(400)
            check("model_jump_filters", await page.evaluate(
                "document.getElementById('models-filter').value === 'llama' && Array.from(document.querySelectorAll('#models-table tr[data-mid]')).every(r => r.dataset.mid.toLowerCase().includes('llama'))"))
            await page.fill("#models-filter", "")
            await page.dispatch_event("#models-filter", "input")
        await page.click('[data-sg-nav="general"]')
        await page.fill("#sg-search", "qqzzxx")
        await page.wait_for_timeout(200)
        check("empty_state", await page.evaluate("!document.getElementById('sg-empty').hidden"))
        await page.fill("#sg-search", "")
        await page.dispatch_event("#sg-search", "input")
        await page.wait_for_timeout(200)

        # A switch saves a boolean, the row shows "changed", the restore button brings the default back.
        await page.click('[data-sg-nav="routing"]')
        await page.click("#sg-sec-routing details.sg-more summary")
        await page.click("#set-strict")
        await wait_for(page, "document.getElementById('settings-status').dataset.state === 'saved'")
        check("switch_saved", get_settings(base)["strict_models"] is True)
        check("row_marked_modified", await page.evaluate("document.getElementById('set-strict').closest('.sg-row').classList.contains('is-modified')"))
        await page.click('#set-strict >> xpath=ancestor::div[contains(@class,"sg-row")]//button[contains(@class,"sg-reset")]', force=True)
        await page.wait_for_timeout(600)
        check("restore_default", get_settings(base)["strict_models"] is False)

        # Segmented host control writes the real field and can be restored.
        await page.click('[data-sg-nav="network"]')
        await page.click('.sg-seg[data-for="set-host-mode"] [data-v="0.0.0.0"]')
        await wait_for(page, "!!document.querySelector('#modal-back.visible #m-yes')")
        check("lan_asks_first", get_settings(base)["host"] == "127.0.0.1")
        await page.click("#m-no")
        await page.wait_for_timeout(300)
        check("lan_cancel_keeps_segment", await page.evaluate(
            "document.querySelector('.sg-seg[data-for=\\'set-host-mode\\'] .active').dataset.v === '127.0.0.1'"))
        await page.click('.sg-seg[data-for="set-host-mode"] [data-v="custom"]')
        check("custom_host_row", await page.evaluate("!document.getElementById('sg-host-custom').hidden"))
        await page.fill("#set-host", "localhost")
        await page.press("#set-host", "Enter")
        await page.dispatch_event("#set-host", "change")
        await page.wait_for_timeout(600)
        check("custom_host_saved", get_settings(base)["host"] == "localhost")
        await page.click('#set-host >> xpath=ancestor::div[contains(@class,"sg-row")]//button[contains(@class,"sg-reset")]', force=True)
        await page.wait_for_timeout(600)
        check("host_restored", get_settings(base)["host"] == "127.0.0.1")
        check("segment_back_to_local", await page.evaluate(
            "document.querySelector('.sg-seg[data-for=\\'set-host-mode\\'] .active').dataset.v === '127.0.0.1' && document.getElementById('sg-host-custom').hidden"))

        # Theme (local only) and language.
        await page.click('[data-sg-nav="general"]')
        await page.click('.sg-seg[data-for="sg-theme"] [data-v="light"]')
        check("theme_light", await page.evaluate("document.documentElement.dataset.theme === 'light' && localStorage.getItem('oh_theme') === 'light'"))
        if out:
            await page.screenshot(path=f"{out}/browser-light.png")
        await page.click('.sg-seg[data-for="sg-theme"] [data-v="dark"]')
        check("theme_dark", await page.evaluate("!document.documentElement.dataset.theme"))
        await page.click('.sg-seg[data-for="lang-select-2"] [data-v="en"]')
        await page.wait_for_timeout(300)
        check("english_labels", await page.evaluate(
            "document.querySelector('[data-sg-nav=\"general\"] span').textContent === 'General' && document.getElementById('lang-select').value === 'en'"))
        check("english_html_desc", await page.evaluate(
            "document.querySelector('[data-i18n-html=\"sg.prefix.d\"] code')?.textContent === 'genspark-claude-opus-5'"))
        check("english_provider_labels", await page.evaluate(
            "!/[А-Яа-я]/.test(document.querySelector('#provider-settings .sg-label')?.textContent || '')"))
        await page.click('.sg-seg[data-for="lang-select-2"] [data-v="ru"]')
        await page.wait_for_timeout(300)

        # Public access tab: same rows, the controls still save, «Дополнительно» stays open after a save.
        await page.click('[data-tab="public"]')
        await wait_for(page, "!!document.getElementById('pub-cors')")
        await page.click("#pub-https summary")
        before = await page.evaluate("document.getElementById('pub-cors').checked")
        await page.click("#pub-cors")
        await page.wait_for_timeout(700)
        with urllib.request.urlopen(base + "/api/admin/public", timeout=10) as response:
            public = json.load(response)["settings"]
        check("public_cors_saved", public["cors"] is (not before), public.get("cors"))
        check("public_advanced_stays_open", await page.evaluate("document.getElementById('pub-https').open"))
        await page.click("#pub-cors")
        await page.wait_for_timeout(700)
        if out:
            await page.screenshot(path=f"{out}/browser-public.png", full_page=True)
        await page.click('[data-tab="settings"]')

        # Narrow window: the rail turns into a horizontal strip.
        await page.set_viewport_size({"width": 760, "height": 900})
        await page.wait_for_timeout(200)
        check("narrow_rail_row", await page.evaluate("getComputedStyle(document.getElementById('sg-nav')).flexDirection === 'row'"))
        if out:
            await page.screenshot(path=f"{out}/browser-narrow.png")
        check("no_console_errors", not errors, errors[:5])
        await browser.close()
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if failures:
        print("FAILED:\n" + "\n".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    target = sys.argv[1].rstrip("/")
    folder = sys.argv[2] if len(sys.argv) > 2 else None
    if folder:
        os.makedirs(folder, exist_ok=True)
    sys.exit(asyncio.run(main(target, folder)))
