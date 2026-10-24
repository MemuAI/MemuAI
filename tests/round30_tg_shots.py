"""Round 30: the Telegram Mini App (app/tgapp) as Telegram opens it — a phone, the small Telegram Desktop window
and computer windows (1280×800, 1920×1080), ru/en, dark/light. Besides every tab: a chat with an answer of the mock
model, the «New MemuCode session» sheet and the shortcut help (computers).

    PYTHONUTF8=1 python3 tests/round30_tg_shots.py OUT_DIR [--lang ru,en] [--sizes phone,tdsmall,desktop,full]

Real servers (tests/ui_lang_scan.Servers) over a temporary data dir; the Mini App is opened with a signed initData
(as Telegram does). Every tab is opened and screenshot (tg-<size>-<lang>-<theme>-<tab>.png). Checks (exit code 1):
page errors / console errors / HTTP >= 400, horizontal overflow of the page, Cyrillic in English.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

SIZES = {"phone": (390, 844, "android"), "tdsmall": (390, 844, "tdesktop"), "desktop": (1280, 800, "tdesktop"),
         "full": (1920, 1080, "tdesktop")}
THEMES = {"dark": {"bg_color": "#17212b", "text_color": "#f5f5f5", "hint_color": "#708499", "button_color": "#5288c1",
                   "button_text_color": "#ffffff", "secondary_bg_color": "#232e3c"},
          "light": {"bg_color": "#ffffff", "text_color": "#000000", "hint_color": "#999999", "button_color": "#2481cc",
                    "button_text_color": "#ffffff", "secondary_bg_color": "#f1f1f1"}}
CHECK_JS = r"""() => {
  const out = { overflow: document.documentElement.scrollWidth > innerWidth + 2 ? document.documentElement.scrollWidth : 0, cyr: [], wide: [] };
  document.querySelectorAll('body *').forEach((n) => { const r = n.getBoundingClientRect(); if (!r.width || !r.height) return;
    const cs = getComputedStyle(n); if (cs.display === 'none' || cs.visibility === 'hidden') return;
    if (r.right > innerWidth + 2 && cs.position !== 'fixed') { let p = n.parentElement, ok = false;
      while (p) { if (/(auto|scroll|hidden)/.test(getComputedStyle(p).overflowX)) { ok = true; break; } p = p.parentElement; }
      if (!ok) out.wide.push((n.className && String(n.className).slice(0, 40)) || n.tagName); } });
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (w.nextNode()) { const t = w.currentNode, p = t.parentElement; if (!p || p.closest('script,style,textarea,.chat-cell,.convo,.search-hit')) continue;   // user content (mock answers) may be Russian
    if (!/[А-Яа-яЁё]/.test(t.nodeValue)) continue; if (!p.getClientRects().length) continue;
    const v = t.nodeValue.trim(); if (/^Русский/.test(v)) continue; out.cyr.push(v.slice(0, 50)); }
  out.wide = [...new Set(out.wide)].slice(0, 6); out.cyr = [...new Set(out.cyr)].slice(0, 8); return out; }"""


def extra_shots(page, out: Path, tag: str, problems: list) -> None:
    """A chat with an answer, the «New session» sheet, the shortcut help on computers."""
    page.evaluate("window.MemuTgApp.switchTab('chats')")
    page.evaluate("window.MemuTgApp.openChat(null)")
    page.wait_for_timeout(500)
    page.fill("#msg-input", "Hello / Привет")
    page.click("#send-btn")
    page.wait_for_timeout(2500)
    page.screenshot(path=str(out / f"tg-{tag}-chat.png"))
    if page.evaluate("window.MemuTgApp.state.features.code"):
        page.evaluate("window.MemuTgApp.switchTab('code')")
        page.wait_for_timeout(600)
        page.click("#code-new")
        page.wait_for_timeout(700)
        if not page.query_selector(".sheet.open #ns-folder-row"):
            problems.append(f"{tag}: «New session» did not open")
        page.screenshot(path=str(out / f"tg-{tag}-newsession.png"))
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
    if page.evaluate("window.MemuDevice.device") == "desktop":
        page.keyboard.press("?")
        page.wait_for_timeout(500)
        page.screenshot(path=str(out / f"tg-{tag}-keys.png"))
        page.keyboard.press("Escape")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--lang", default="ru,en")
    ap.add_argument("--sizes", default="phone,tdsmall,desktop,full")
    ap.add_argument("--themes", default="dark,light")
    a = ap.parse_args()
    os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memu-r30-tg-"))
    from playwright.sync_api import sync_playwright
    from ui_lang_scan import Servers
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    servers = Servers().start()
    problems: list[str] = []
    try:
        from app import panels, pgdata, tg_webapp
        tg_webapp.save_config({"enabled": True, "mode": "cloudflare", "code_control": True})
        with sync_playwright() as play:
            browser = play.chromium.launch(executable_path=os.environ.get("MEMU_TEST_CHROMIUM", "/usr/bin/chromium"), args=["--no-sandbox"])
            for lang in a.lang.split(","):
                pgdata.save_preferences({"telegram_bot_token": servers.TG_TOKEN, "telegram_chat_id": str(servers.TG_USER), "language": lang})
                panels.update_settings({"lang": lang})
                for theme in a.themes.split(","):
                    init = tg_webapp.sign_init_data({"auth_date": int(time.time()), "query_id": "AAH-shots",
                                                     "user": {"id": servers.TG_USER, "first_name": "Yono", "language_code": lang}}, servers.TG_TOKEN)
                    for size in a.sizes.split(","):
                        w, h, platform = SIZES[size]
                        frag = urllib.parse.urlencode({"tgWebAppData": init, "tgWebAppVersion": "8.0", "tgWebAppPlatform": platform,
                                                       "tgWebAppThemeParams": json.dumps(THEMES[theme])})
                        mobile = platform == "android"
                        ctx = browser.new_context(viewport={"width": w, "height": h}, bypass_csp=True,
                                                  is_mobile=mobile, has_touch=mobile)
                        page = ctx.new_page()
                        tag = f"{size}-{lang}-{theme}"
                        page.on("pageerror", lambda e, tag=tag: problems.append(f"{tag} pageerror: {e}"))
                        page.on("console", lambda m, tag=tag: m.type == "error" and problems.append(f"{tag} console: {m.text[:200]}"))
                        page.on("response", lambda r, tag=tag: r.status >= 400 and problems.append(f"{tag} HTTP {r.status} {r.url[:120]}"))
                        page.goto(f"{servers.tg}/tgapp/#{frag}")
                        try:
                            page.wait_for_selector("#app:not([hidden]) .tab", timeout=20000)
                        except Exception as exc:  # noqa: BLE001
                            problems.append(f"{tag}: did not open: {str(exc)[:120]}")
                            page.screenshot(path=str(out / f"tg-{tag}-boot.png"))
                            ctx.close()
                            continue
                        page.wait_for_timeout(900)
                        tabs = page.evaluate("[...document.querySelectorAll('.tab[data-tab]')].map((b) => b.dataset.tab)")
                        for tab in tabs:
                            page.evaluate(f"document.querySelector('.tab[data-tab=\"{tab}\"]').click()")
                            page.wait_for_timeout(900)
                            res = page.evaluate(CHECK_JS)
                            if res["overflow"] or res["wide"]:
                                problems.append(f"{tag} {tab}: overflow {res['overflow']} {res['wide']}")
                            if lang == "en" and res["cyr"]:
                                problems.append(f"{tag} {tab}: Cyrillic {res['cyr']}")
                            page.screenshot(path=str(out / f"tg-{tag}-{tab}.png"))
                        extra_shots(page, out, tag, problems)
                        ctx.close()
            browser.close()
    finally:
        servers.stop()
    for line in problems:
        print("FAIL:", line)
    print(f"done: {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
