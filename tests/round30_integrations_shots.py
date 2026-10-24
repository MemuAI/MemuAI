"""Round 30 (r30-integrations): headless screenshots of Settings → Integrations (Google, Notion, GitHub).

    PYTHONUTF8=1 python3 tests/round30_integrations_shots.py OUT_DIR [--lang ru,en] [--theme dark,light]
        [--design classic] [--size 1366x768] [--seed none|connected]

Real servers (tests/ui_lang_scan.Servers) over a temporary data dir.  ``--seed connected`` fakes a connected
Google account (a refresh token in the sync secret store + recorded scopes for Gmail and Calendar) and a Notion
internal token, so the «connected» states are drawn without any network: the status endpoints read local state only.
Checks per state: no page errors, no console errors, no clipped text inside the integrations pane, and in English
no Cyrillic in it.  Exit code 1 on a problem.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

DESIGN_JS = """([design, theme]) => { try { setTheme(theme, false); } catch (e) {}
  if (design === 'classic' || design === 'default') delete document.documentElement.dataset.design; else document.documentElement.dataset.design = design; }"""

CHECK_JS = """(lang) => {
  const pane = document.querySelector('#panel .settings-tab-pane.active');
  if (!pane) return {clip: [], cyr: [], missing: true};
  const clip = [], cyr = [];
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
  pane.querySelectorAll('button, .sx-label, .gi-title, .gi-svc-title, .gi-pill, .sx-sub-title, a.pbtn').forEach((el) => {
    if (!visible(el)) return;
    const cs = getComputedStyle(el);
    if (el.scrollWidth > el.clientWidth + 1 && cs.textOverflow !== 'ellipsis' && cs.overflow !== 'visible') clip.push(el.textContent.trim().slice(0, 60));
  });
  if (lang === 'en') {
    const walker = document.createTreeWalker(pane, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      const el = node.parentElement;
      if (el && visible(el) && /[А-Яа-яЁё]/.test(node.textContent)) cyr.push(node.textContent.trim().slice(0, 60));
    }
  }
  return {clip, cyr};
}"""


def states() -> list[tuple[str, str, int]]:
    return [
        ("apps", "showSettings(); openSettingsPage('apps')", 1500),
        ("google", "showSettings(); openSettingsPage('apps'); setTimeout(() => MemuSettingsKit.openSub('int-google'), 500)", 1800),
        ("google-client", "showSettings(); openSettingsPage('apps'); setTimeout(() => { MemuSettingsKit.openSub('int-google'); setTimeout(() => { const d = document.querySelector('#gi-client-more'); if (d) { d.open = true; d.scrollIntoView({block: 'start'}); } }, 300); }, 500)", 2000),
        ("notion", "showSettings(); openSettingsPage('apps'); setTimeout(() => MemuSettingsKit.openSub('int-notion'), 500)", 1800),
        ("github", "showSettings(); openSettingsPage('apps'); setTimeout(() => MemuSettingsKit.openSub('int-github'), 500)", 1500),
    ]


def seed_connected(data_dir: str, drive: bool = False) -> None:
    """A connected Google account (Gmail + Calendar granted, Drive not yet) and a Notion token — local state only."""
    from app import gdrive_sync_crypto as crypto
    folder = Path(data_dir) / "memuai" / "sync"
    folder.mkdir(parents=True, exist_ok=True)
    store = crypto.SecretStore(folder)
    store.set("google_refresh_token", "1//seeded-refresh-token")
    store.set("google_scopes", " ".join(sorted({
        "openid", "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.compose",
        "https://www.googleapis.com/auth/calendar.readonly", "https://www.googleapis.com/auth/calendar.events"}
        | ({"https://www.googleapis.com/auth/drive.readonly", "https://www.googleapis.com/auth/drive.file"} if drive else set()))))
    (folder / "settings.json").write_text(json.dumps({"client_id": "1234-demo.apps.googleusercontent.com"}), encoding="utf-8")
    (folder / "state.json").write_text(json.dumps({"account": {"email": "alex.demo@gmail.com", "name": "Alex Demo"}}), encoding="utf-8")
    memu = Path(data_dir) / "memuai"
    memu.mkdir(parents=True, exist_ok=True)
    store.set("notion_token", "ntn_" + "a" * 40)
    (memu / "integrations.json").write_text(json.dumps({
        "google": {"services": ["gmail", "calendar", "drive"]},
        "notion": {"mode": "token", "account": "Alex's Notion", "checked": 1700000000}}), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--lang", default="ru,en")
    ap.add_argument("--theme", default="dark,light")
    ap.add_argument("--design", default="classic")
    ap.add_argument("--size", default="1366x768")
    ap.add_argument("--seed", default="none", choices=["none", "connected"])
    ap.add_argument("--states", default="")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    data = tempfile.mkdtemp(prefix="memu-r30-int-")
    os.environ["MEMUAI_DATA_DIR"] = data
    if a.seed == "connected":
        seed_connected(data)
    import httpx
    from playwright.sync_api import sync_playwright
    from ui_lang_scan import Servers
    servers = Servers().start()
    w, h = (int(x) for x in a.size.split("x"))
    only = set(filter(None, a.states.split(",")))
    problems: list[str] = []
    try:
        with sync_playwright() as play:
            browser = play.chromium.launch(executable_path=os.environ.get("MEMU_TEST_CHROMIUM", "/workspace/work/chromium/chrome"),
                                           args=["--no-sandbox"])
            for lang in a.lang.split(","):
                with httpx.Client(timeout=30, trust_env=False) as client:
                    client.post(servers.pg + "/api/pg/preferences", json={"profile_onboarded": True, "profile_name": "Alex",
                                                                          "language": lang, "updates_enabled": False})
                for theme in a.theme.split(","):
                    ctx = browser.new_context(viewport={"width": w, "height": h}, bypass_csp=True)
                    ctx.add_init_script(f"try {{ localStorage.setItem('memu_lang', '{lang}'); }} catch (_) {{}}")
                    page = ctx.new_page()
                    cur = {"state": "boot"}
                    page.on("console", lambda m: m.type == "error" and problems.append(f"{lang}/{theme} [{cur['state']}] console: {m.text[:200]}"))
                    page.on("pageerror", lambda e: problems.append(f"{lang}/{theme} [{cur['state']}] pageerror: {str(e)[:240]}"))
                    page.goto(servers.pg + "/")
                    page.wait_for_function("document.body.classList.contains('boot-ready')", timeout=90000)
                    page.wait_for_timeout(800)
                    page.evaluate(DESIGN_JS, [a.design, theme])
                    for name, code, wait in states():
                        if only and name not in only:
                            continue
                        cur["state"] = name
                        page.evaluate("() => { try { closePanel(); } catch (e) {} }")
                        page.wait_for_timeout(200)
                        page.evaluate("() => { " + code + " }")
                        page.wait_for_timeout(wait)
                        res = page.evaluate(CHECK_JS, lang)
                        for key in ("clip", "cyr"):
                            for text in res.get(key) or []:
                                problems.append(f"{lang}/{theme} [{name}] {key}: {text}")
                        page.screenshot(path=str(out / f"{a.design}-{theme}-{lang}-{w}-{a.seed}-{name}.png"))
                    ctx.close()
            browser.close()
    finally:
        servers.stop()
    for line in problems:
        print(line)
    print("problems:", len(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
