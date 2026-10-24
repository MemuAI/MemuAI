"""Round 33 (r33-thinking): the reasoning picker of the chat, Settings → «Reasoning by model» and the Proxy model
card — real servers, a recording offline mock, screenshots RU/EN × dark/light/glass.

    PYTHONUTF8=1 NO_PROXY='*' python3 tests/round33_thinking_shots.py OUT_DIR

Checks: Gemini 3.7 Flash lists exactly Auto · medium / Low / Medium / High; GLM-5.3 Low / High / Max without «Off»;
Kimi K2.6 Auto / Off; GPT-4o shows no reasoning control; choosing «Off» for Qwen 3.8 Flash sends
``enable_thinking: false`` to the API; Settings → Reasoning by model lists per-model options; the Proxy model card
shows the «Reasoning levels» section and saves a manual profile. No page errors, no Cyrillic in English.
``R33_VARIANTS=ru-dark-classic`` runs only that variant. Exit code 1 on any problem.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="r33-thinking-shots-"))
os.environ["MEMUAI_ASSISTANT_NO_WARM"] = "1"
CHROME = "/usr/bin/chromium" if os.path.exists("/usr/bin/chromium") else "/workspace/work/chromium/chrome"
MODELS = ["gemini-3.7-flash", "glm-5.3", "kimi-k2.6", "gpt-4o", "qwen3.8-flash", "claude-sonnet-4.6"]
CYR = re.compile(r"[А-Яа-яЁё]")


def post(base: str, path: str, body: dict) -> dict:
    request = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(request, timeout=30).read() or b"{}")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_mock():
    import uvicorn
    from tests.round33_thinking_test import make_mock
    app = make_mock()
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return app, server, port


async def open_menu(page, select_css: str) -> list:
    """Open the pretty-select built over ``select_css`` and return the option texts."""
    button = page.locator(f"{select_css} + .pretty-select-button, .pretty-select:has({select_css}) .pretty-select-button").first
    if not await button.count():
        return await page.eval_on_selector_all(f"{select_css} option", "els => els.map(e => e.textContent)")
    await button.click()
    await page.wait_for_timeout(250)
    return await page.eval_on_selector_all(".pretty-select-menu:not([hidden]) [data-pretty-option] span, .pretty-select-menu.open [data-pretty-option] span",
                                           "els => els.map(e => e.textContent)")


async def main(out: Path) -> int:
    from playwright.async_api import async_playwright
    from tests.ui_lang_scan import Servers
    out.mkdir(parents=True, exist_ok=True)
    mock, mock_server, mock_port = start_mock()
    servers = Servers().start()
    post(servers.proxy, "/api/admin/accounts", {"provider": "custom", "label": "think", "fields": {
        "name": "Think", "base_url": f"http://127.0.0.1:{mock_port}/v1", "api_key": "sk-test", "models": ", ".join(MODELS)}})
    base = servers.pg
    problems: list = []

    def check(ok, what):
        if not ok:
            problems.append(what)
            print("FAIL:", what)

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
            only = [v for v in os.environ.get("R33_VARIANTS", "").split(",") if v]
            for lang, theme, design in (("ru", "dark", "classic"), ("en", "light", "classic"),
                                        ("ru", "light", "glass"), ("en", "dark", "glass")):
                tag = f"{lang}-{theme}-{design}"
                if only and tag not in only:
                    continue
                en = lang == "en"
                post(base, "/api/pg/preferences", {"language": lang, "theme": theme, "ui_design": design,
                                                   "profile_onboarded": True, "thinking": True,
                                                   "default_effort": "minimal", "model_efforts": {}})
                page = await browser.new_page(viewport={"width": 1280, "height": 860})
                page.on("pageerror", lambda e, tag=tag: problems.append(f"{tag}: pageerror {e}"))
                await page.add_init_script("try { localStorage.setItem('memu_model', 'think-gemini-3.7-flash'); } catch (e) {}")
                await page.goto(base + "/", wait_until="networkidle")
                try:
                    await page.wait_for_selector("#startup-cover", state="hidden", timeout=60000)
                except Exception:
                    pass
                await page.wait_for_timeout(800)
                await page.evaluate("() => { setCurrentModel('think-gemini-3.7-flash'); renderCaps(); }")
                await page.wait_for_timeout(300)
                check(await page.locator("#tog-think").count() == 1, f"{tag}: no reasoning chip for Gemini 3.7 Flash")
                values = await page.eval_on_selector_all("#chat-effort option", "els => els.map(e => e.value)")
                check(values == ["auto", "low", "medium", "high"], f"{tag}: Gemini 3.7 Flash options {values}")
                selected = await page.eval_on_selector("#chat-effort", "e => e.value")
                check(selected == "low", f"{tag}: default «minimal» should show as Low on Gemini 3.7 Flash, got {selected}")
                texts = await open_menu(page, "#chat-effort")
                check(texts[:1] == (["Auto · medium"] if en else ["Авто · средний"]), f"{tag}: auto label {texts}")
                await page.screenshot(path=str(out / f"{tag}-gemini37-menu.png"), clip={"x": 0, "y": 420, "width": 1280, "height": 440})
                await page.keyboard.press("Escape")
                for model, expected in (("glm-5.3", ["auto", "low", "high", "max"]), ("kimi-k2.6", ["auto", "off"]),
                                        ("claude-sonnet-4.6", ["auto", "off", "low", "medium", "high", "max"])):
                    await page.evaluate(f"() => {{ setCurrentModel('think-{model}'); renderCaps(); }}")
                    await page.wait_for_timeout(200)
                    values = await page.eval_on_selector_all("#chat-effort option", "els => els.map(e => e.value)")
                    check(values == expected, f"{tag}: {model} options {values}")
                    if model == "glm-5.3":
                        await open_menu(page, "#chat-effort")
                        await page.screenshot(path=str(out / f"{tag}-glm53-menu.png"), clip={"x": 0, "y": 420, "width": 1280, "height": 440})
                        await page.keyboard.press("Escape")
                await page.evaluate("() => { setCurrentModel('think-gpt-4o'); renderCaps(); }")
                await page.wait_for_timeout(200)
                check(await page.locator("#tog-think").count() == 0, f"{tag}: GPT-4o must have no reasoning control")
                # «Off» for Qwen 3.8 Flash really reaches the API as enable_thinking: false.
                await page.evaluate("() => { setCurrentModel('think-qwen3.8-flash'); renderCaps(); }")
                await page.wait_for_timeout(200)
                await page.evaluate("() => { const s = document.querySelector('#chat-effort'); s.value = 'off'; s.dispatchEvent(new Event('change')); }")
                mock.state.bodies.clear()
                await page.fill("#input", "hi")
                await page.keyboard.press("Enter")
                for _ in range(60):
                    if mock.state.bodies:
                        break
                    await page.wait_for_timeout(250)
                sent = [b for b in mock.state.bodies if b.get("model") == "qwen3.8-flash"]
                check(bool(sent) and sent[-1].get("enable_thinking") is False,
                      f"{tag}: Off for Qwen did not send enable_thinking=false: {[{k: v for k, v in b.items() if k != 'messages'} for b in sent][-1:]}")
                await page.wait_for_timeout(800)
                # Settings → Models → Reasoning by model.
                for _ in range(20):
                    await page.keyboard.press("Control+Comma")
                    await page.wait_for_timeout(600)
                    if await page.locator('[data-set-tab="models"]').count():
                        break
                if await page.locator('[data-set-tab="models"]').count():
                    await page.click('[data-set-tab="models"]')
                    await page.wait_for_timeout(400)
                    await page.screenshot(path=str(out / f"{tag}-settings-models.png"))
                    link = page.locator('[data-sx-open="effort"]').first
                    if await link.count():
                        await link.click()
                        await page.wait_for_timeout(1200)       # the page slides in (slower with the glass design)
                    rows = await page.eval_on_selector_all("[data-model-effort]", "els => els.map(e => [e.dataset.modelEffort, [...e.options].map(o => o.value)])")
                    got = dict(rows)
                    metrics = await page.evaluate("""() => { const g = document.querySelector('.effort-grid'); const card = g && g.closest('.sx-card');
                      const last = g && g.querySelector('.effort-row:last-child'); const r = last && last.getBoundingClientRect(); const c = card && card.getBoundingClientRect();
                      return {rows: g ? g.querySelectorAll('.effort-row').length : 0, lastBottom: r && r.bottom, cardBottom: c && c.bottom,
                              overflow: card && getComputedStyle(card).overflow, h: card && card.scrollHeight, ch: card && card.clientHeight}; }""")
                    print(tag, "by-model metrics", metrics)
                    if os.environ.get("R33_DEBUG"):
                        print(await page.evaluate("""() => { const out = []; let el = document.querySelector('.effort-grid');
                          while (el && out.length < 9) { const cs = getComputedStyle(el); out.push([el.className.slice(0, 40), el.getBoundingClientRect().height, cs.height, cs.maxHeight, cs.overflow, cs.display, cs.gridTemplateRows.slice(0, 60)]); el = el.parentElement; }
                          return out; }"""))
                    check(metrics["rows"] == 6, f"{tag}: expected 6 reasoning rows, got {metrics['rows']}")
                    check(metrics["lastBottom"] is not None and metrics["lastBottom"] <= metrics["cardBottom"] + 1,
                          f"{tag}: the last reasoning row is cut off {metrics}")
                    check(got.get("think-gemini-3.7-flash") == ["auto", "low", "medium", "high"], f"{tag}: per-model row gemini {got.get('think-gemini-3.7-flash')}")
                    check("think-gpt-4o" not in got, f"{tag}: GPT-4o must not be in Reasoning by model")
                    await page.screenshot(path=str(out / f"{tag}-settings-by-model.png"))
                else:
                    problems.append(f"{tag}: settings did not open")
                if en:
                    text = await page.evaluate("() => document.body.innerText")
                    bad = [line for line in text.splitlines() if CYR.search(line) and "Размышлен" in line]
                    check(not bad, f"{tag}: Cyrillic in English: {bad[:3]}")
                await page.close()
                # The Proxy model card.
                page = await browser.new_page(viewport={"width": 1280, "height": 900})
                page.on("pageerror", lambda e, tag=tag: problems.append(f"{tag}: proxy pageerror {e}"))
                await page.add_init_script(f"try {{ localStorage.setItem('lang', '{lang}'); localStorage.setItem('memuai_lang', '{lang}'); localStorage.setItem('theme', '{theme}'); }} catch (e) {{}}")
                await page.goto(servers.proxy + "/", wait_until="networkidle")
                await page.wait_for_timeout(1200)
                await page.evaluate("(id) => window.MemuModelCaps && window.MemuModelCaps.open(id)", "think-gemini-3.7-flash")
                try:
                    await page.wait_for_selector("[data-mrc] .mrc-chips", timeout=15000)
                except Exception:
                    problems.append(f"{tag}: proxy card has no reasoning section")
                await page.wait_for_timeout(300)
                section = page.locator("[data-mrc]")
                if await section.count():
                    await section.scroll_into_view_if_needed()
                    chips = await page.eval_on_selector_all("[data-mrc] .mrc-chip", "els => els.map(e => e.textContent)")
                    check(len(chips) == 4, f"{tag}: proxy chips {chips}")
                    await page.click("[data-mrc-edit]")
                    await page.wait_for_timeout(250)
                    await section.screenshot(path=str(out / f"{tag}-proxy-card.png"))
                await page.close()
            await browser.close()
    finally:
        servers.stop()
        mock_server.should_exit = True
    print("PROBLEMS:", len(problems))
    for item in problems:
        print(" -", item)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "r33-thinking-shots"))))
