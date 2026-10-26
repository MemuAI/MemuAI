"""Round 27 (r27-wakeword): headless screenshots + checks of the wake word UI against YOUR dev server.

    PYTHONUTF8=1 NO_PROXY='*' python tests/round27_wakeword_shots.py http://127.0.0.1:PORT OUT_DIR

Uses a private data copy (changes preferences through /api/pg/preferences). /api/wake/* answers are mocked with
page.route, so every state is shown without a microphone or a model:
  Settings → Quick Ask → «Слово-активатор»: off + model not downloaded, downloading 42 %, listening with the
  sound-alike warning, a bad phrase, a microphone error, vosk missing, the «Test» meter after the phrase was heard;
  ru/en, dark/light, classic/glass/grid. The Quick Ask window: listening strip (+ live words) and transcribing.
Checks (exit 1): the sub-page link exists and opens a non-empty page, the toggle/phrase/model/test controls exist,
no element sticks out of the pane, no page errors, the window strip appears on MemuQuick.emit("wake") and the
captured audio goes to /api/voice/transcribe and the text is sent.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
import urllib.request
from pathlib import Path

CHROME = os.environ.get("MEMU_TEST_CHROMIUM") or (
    "/usr/bin/chromium" if os.path.exists("/usr/bin/chromium") else "/workspace/work/chromium/chrome")

BASE_STATE = {
    "settings": {"enabled": False, "phrase": "", "lang": "ru", "device": "", "sensitivity": 50, "cooldown": 3,
                 "chime": True, "glow": True},
    "status": {"state": "off", "reason": "", "error": None, "level": 0, "last_hit": None, "testing": False,
               "enabled": False, "phrase": "привет мему", "lang": "ru", "quick_enabled": True},
    "models": [{"lang": "ru", "id": "vosk-model-small-ru-0.22", "size": 46236750, "title": {"ru": "Русский", "en": "Russian"},
                "phrase": "привет мему", "ready": False, "download": None},
               {"lang": "en", "id": "vosk-model-small-en-us-0.15", "size": 41205931, "title": {"ru": "Английский", "en": "English"},
                "phrase": "hey memu", "ready": False, "download": None}],
    "engine": {"available": True, "installable": False, "install": {"state": "idle", "error": ""}},
    "mic": {"backend": "winmm", "devices": ["Микрофон (Realtek(R) Audio)", "Гарнитура (USB Audio Device)"]},
    "defaults": {"ru": "привет мему", "en": "hey memu"},
    "ranges": {"sensitivity": [0, 100], "cooldown": [1, 30]},
}
PHRASE_OK = {"phrase": "привет мему", "checked": True, "ok": True, "problem": None, "variants": [["привет", "мемо"]],
             "words": [{"word": "привет", "known": True, "alias": []}, {"word": "мему", "known": False, "alias": ["мемо"]}]}
PHRASE_UNCHECKED = {"phrase": "привет мему", "checked": False, "ok": True, "problem": None, "variants": [], "words": []}

PHRASE_OK_EN = {**PHRASE_OK, "phrase": "hey memu", "variants": [["hey", "memo"]],
                "words": [{"word": "hey", "known": True, "alias": []}, {"word": "memu", "known": False, "alias": ["memo"]}]}

def variant(name: str, en: bool):
    state = copy.deepcopy(BASE_STATE)
    phrase = PHRASE_UNCHECKED
    phrase_ok = PHRASE_OK_EN if en else PHRASE_OK
    if en:
        state["settings"]["lang"] = "en"
        state["status"]["phrase"] = "hey memu"
    if name == "downloading":
        state["settings"]["enabled"] = True
        state["status"].update(state="loading", reason="downloading", progress=42, enabled=True)
        state["models"][0 if not en else 1]["download"] = {"state": "downloading", "progress": 42, "done": 19000000,
                                                           "total": 46236750, "speed": 2400000, "error": None}
    elif name in ("listening", "test"):
        for m in state["models"]:
            m["ready"] = True
        state["settings"]["enabled"] = True
        state["status"].update(state="listening", enabled=True)
        phrase = phrase_ok
    elif name == "bad-phrase":
        for m in state["models"]:
            m["ready"] = True
        state["settings"].update(enabled=True, phrase="привет щщщщ")
        problem = {"ru": "Модель не знает слово «щщщщ» и не нашла похожего по звучанию. Выберите другое слово.",
                   "en": "The model does not know “щщщщ” and found nothing that sounds alike. Pick another word."}
        state["status"].update(state="error", reason="phrase", error=problem, enabled=True)
        phrase = {"phrase": "привет щщщщ", "checked": True, "ok": False, "problem": problem, "variants": [], "words": []}
    elif name == "mic-error":
        for m in state["models"]:
            m["ready"] = True
        state["settings"].update(enabled=True, device="Старый микрофон USB")
        state["status"].update(state="error", reason="mic", enabled=True, error={
            "ru": "Микрофон «Старый микрофон USB» не подключён. Выберите другой или «По умолчанию».",
            "en": "The microphone “Старый микрофон USB” is not connected. Pick another one or “Default”.", "code": "no_device"})
        phrase = phrase_ok
    elif name == "no-engine":
        state["engine"] = {"available": False, "installable": True, "install": {"state": "idle", "error": ""}}
        state["status"].update(state="error", reason="no_engine")
        state["models"][0]["download"] = {"state": "error", "progress": 0, "done": 0, "total": 46236750, "speed": 0,
                                          "error": {"ru": "Сервер моделей Vosk ответил HTTP 503. Повторите позже.",
                                                    "en": "The Vosk model server answered HTTP 503. Try again later."}}
    return state, phrase

def set_prefs(base: str, patch: dict) -> None:
    request = urllib.request.Request(base + "/api/pg/preferences", data=json.dumps(patch).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(request, timeout=10).read()

async def mock_wake(page, state, phrase, heard=False):
    status = dict(state["status"])
    if heard:
        status.update(state="listening", reason="test", testing=True, level=0.04,
                      last_hit={"at": 9e12, "conf": 0.93, "phrase": "привет мемо" if state["settings"]["lang"] == "ru" else "hey memo", "test": True})

    async def handle(route):
        url = route.request.url
        body = state
        if "/api/wake/phrase" in url:
            body = phrase
        elif "/api/wake/test" in url:
            body = dict(status, last_hit=None)
        elif "/api/wake/status" in url:
            body = status
        await route.fulfill(status=200, content_type="application/json", body=json.dumps(body, ensure_ascii=False))
    await page.route("**/api/wake/**", handle)

async def open_wake(browser, base, problems, state, phrase, heard=False, viewport=(1180, 2300)):
    page = await browser.new_page(viewport={"width": viewport[0], "height": viewport[1]}, device_scale_factor=1)
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    await mock_wake(page, state, phrase, heard)
    await page.goto(base + "/", wait_until="networkidle")
    await page.wait_for_timeout(800)
    await page.keyboard.press("Control+Comma")
    await page.wait_for_timeout(600)
    await page.click('[data-set-tab="quick"]')
    await page.wait_for_timeout(1200)
    link = page.locator('[data-sx-open="quick-plugin-wake"]')
    if not await link.count():
        problems.append("no «Слово-активатор» link in Quick Ask settings")
        return page
    await link.first.click()
    await page.wait_for_timeout(900)
    return page

async def check_page(page, problems, label):
    info = await page.evaluate("""() => {
        const sub = document.querySelector('[data-sx-sub="quick-plugin-wake"]');
        if (!sub || sub.hidden) return {missing: true};
        const pane = document.querySelector('#pane-quick') || sub;
        const right = pane.getBoundingClientRect().right + 1;
        const wide = [...sub.querySelectorAll('*')].filter((el) => el.offsetParent && el.getBoundingClientRect().right > right
            && !el.closest('.sx-help-pop, .sx-section-head')).slice(0, 5).map((el) => (el.id || el.className || el.tagName) + '');
        return {missing: false, wide, ids: ['wks-enabled', 'wks-phrase', 'wks-test-btn', 'wks-device', 'wks-sens'].filter((id) => !document.getElementById(id)),
                text: sub.innerText.length};
    }""")
    if info.get("missing"):
        problems.append(f"{label}: the wake word sub-page did not open")
        return
    if info["wide"]:
        problems.append(f"{label}: elements stick out of the pane: {info['wide']}")
    if info["ids"]:
        problems.append(f"{label}: controls missing: {info['ids']}")
    if info["text"] < 200:
        problems.append(f"{label}: the page is almost empty")

async def shot_sub(page, path):
    """The settings body scrolls inside a fixed-height dialog: the page top, then each lower section on its own."""
    sub = page.locator('[data-sx-sub="quick-plugin-wake"]')
    if not await sub.count():
        await page.screenshot(path=str(path))
        return
    await sub.screenshot(path=str(path))
    for sid in ("wks-test", "wks-tune"):
        loc = page.locator(f"#{sid}")
        if await loc.count() and await loc.is_visible():
            more = loc.locator(".sx-more")
            if sid == "wks-tune" and await more.count():
                await more.locator("summary").click()
                await page.wait_for_timeout(250)
            await loc.scroll_into_view_if_needed()
            await page.wait_for_timeout(150)
            await loc.screenshot(path=str(path).replace(".png", f"-{sid}.png"))

async def window_shots(browser, base, out, problems):
    page = await browser.new_page(viewport={"width": 720, "height": 420}, device_scale_factor=1)
    page.on("pageerror", lambda e: problems.append(f"window pageerror: {e}"))
    calls = {"phase": "listening", "transcribe": 0, "end": []}

    async def wake(route):
        url = route.request.url
        if "/api/wake/session/end" in url:
            calls["end"].append(json.loads(route.request.post_data or "{}"))
            return await route.fulfill(status=200, content_type="application/json", body='{"ok": true}')
        if "/api/wake/session" in url:
            session = {"id": 7, "phase": calls["phase"], "level": 0.035, "heard": True, "partial": "какая сегодня", "seconds": 1.4}
            if calls["phase"] == "captured":
                session.update(text="какая сегодня погода", audio="data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAgD4AAAB9AAACABAAZGF0YQAAAAA=")
            return await route.fulfill(status=200, content_type="application/json", body=json.dumps({"session": session}, ensure_ascii=False))
        return await route.fulfill(status=200, content_type="application/json", body='{"ok": true}')

    async def transcribe(route):
        calls["transcribe"] += 1
        await asyncio.sleep(0.6)
        await route.fulfill(status=200, content_type="application/json", body=json.dumps({"text": "Какая сегодня погода?"}, ensure_ascii=False))

    sent = []

    async def ask(route):
        sent.append(json.loads(route.request.post_data or "{}").get("text"))
        await route.fulfill(status=200, content_type="text/event-stream", body='data: {"type":"delta","text":"Сейчас +12°, облачно."}\n\ndata: {"type":"done"}\n\n')
    await page.route("**/api/wake/**", wake)
    await page.route("**/api/voice/transcribe", transcribe)
    await page.route("**/api/assistant/run", lambda route: route.fulfill(status=200, content_type="application/json", body='{"matched": false}'))
    await page.route("**/api/quick/ask", ask)
    await page.goto(base + "/quick/index.html", wait_until="networkidle")
    await page.wait_for_timeout(900)
    await page.evaluate("window.QuickAsk && QuickAsk.onShow({fresh:true, maxHeight:420, rounded:true, corner:'dwm', radius:8, pinned:false, source:'wake', page:null})")
    await page.evaluate("window.MemuQuick.emit('wake', {id: 7, glow: true})")
    await page.wait_for_timeout(700)
    visible = await page.evaluate("""() => { const el = document.querySelector('.mqw-host'); return !!el && !el.closest('[hidden]') && el.offsetHeight > 0; }""")
    if not visible:
        problems.append("window: the listening strip did not appear on emit('wake')")
    value = await page.evaluate("document.getElementById('qa-input') && document.getElementById('qa-input').value")
    if value != "какая сегодня":
        problems.append(f"window: live words not in the input ({value!r})")
    await page.screenshot(path=str(out / "window-listening.png"))
    calls["phase"] = "captured"
    await page.wait_for_timeout(350)
    await page.screenshot(path=str(out / "window-transcribing.png"))
    await page.wait_for_timeout(1500)
    if calls["transcribe"] != 1:
        problems.append(f"window: /api/voice/transcribe called {calls['transcribe']} times")
    if not any(c.get("action") == "done" for c in calls["end"]):
        problems.append(f"window: the session was not ended ({calls['end']})")
    await page.screenshot(path=str(out / "window-after.png"))
    if sent and sent[0] != "Какая сегодня погода?":
        problems.append(f"window: sent {sent!r}")
    state = await page.evaluate("document.documentElement.dataset.mqwState || ''")
    if state:
        problems.append(f"window: state left behind: {state}")
    await page.close()

async def main(base: str, out: Path) -> int:
    from playwright.async_api import async_playwright
    out.mkdir(parents=True, exist_ok=True)
    problems: list = []
    reset = {"profile_onboarded": True, "language": "ru", "quick_ask_enabled": True}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        plan = [("off", "dark", "classic", False), ("downloading", "light", "classic", False), ("listening", "dark", "classic", False),
                ("listening", "light", "glass", False), ("listening", "dark", "grid", False), ("bad-phrase", "light", "classic", False),
                ("mic-error", "dark", "classic", False), ("no-engine", "light", "classic", False),
                ("listening", "light", "classic", True), ("downloading", "dark", "classic", True), ("mic-error", "light", "glass", True)]
        for name, theme, design, en in plan:
            set_prefs(base, dict(reset, theme=theme, ui_design=design, language="en" if en else "ru"))
            state, phrase = variant(name, en)
            page = await open_wake(browser, base, problems, state, phrase)
            label = f"{name}-{design}-{theme}{'-en' if en else ''}"
            await check_page(page, problems, label)
            await shot_sub(page, out / f"settings-{label}.png")
            await page.close()

        set_prefs(base, dict(reset, theme="dark", ui_design="classic"))
        state, phrase = variant("test", False)
        page = await open_wake(browser, base, problems, state, phrase, heard=True)
        await page.click("#wks-test-btn")
        await page.wait_for_timeout(700)
        heard = await page.evaluate("document.getElementById('wks-heard') && document.getElementById('wks-heard').textContent")
        if not heard or "услышана" not in heard:
            problems.append(f"test mode: no «heard» line ({heard!r})")
        await shot_sub(page, out / "settings-test-heard-dark.png")
        await page.close()
        set_prefs(base, dict(reset, theme="light", ui_design="classic"))
        await window_shots(browser, base, out, problems)
        set_prefs(base, dict(reset, theme="dark", ui_design="classic"))
        await browser.close()
    for line in problems:
        print("PROBLEM:", line)
    print(f"shots in {out}; problems: {len(problems)}")
    return 1 if problems else 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1].rstrip("/"), Path(sys.argv[2]))))
