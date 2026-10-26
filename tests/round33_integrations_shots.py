"""Round 33 (r33-integrations): screenshots of Settings → Integrations (Google setup guide, «basic access → Expand
access», «Check APIs» with a disabled Gmail API, GitHub token guide) and of the new confirmation cards.

    PYTHONUTF8=1 python3 tests/round33_integrations_shots.py OUT_DIR [--lang ru,en] [--theme dark,light]
        [--design classic,glass] [--size 1366x768] [--states …]

Real servers (tests/ui_lang_scan.Servers) in this process; Google/GitHub are tests/round33_mock_apis.Fake33 (the
«Check APIs» button really calls them).  Seeds: none (wizard), limited (an old Round 30 grant), full (every service,
Gmail API disabled in the fake project).  Checks: no page/console errors, no clipped text, no Cyrillic in English.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from round30_integrations_shots import CHECK_JS, DESIGN_JS  # noqa: E402

G = "https://www.googleapis.com/auth/"
OLD = {"openid", G + "userinfo.email", G + "gmail.readonly", G + "gmail.compose", G + "calendar.readonly",
       G + "calendar.events", G + "drive.readonly", G + "drive.file"}
FULL = {"openid", G + "userinfo.email", "https://mail.google.com/", G + "gmail.settings.basic", G + "calendar", G + "drive",
        G + "documents", G + "spreadsheets", G + "tasks", G + "contacts"}
OPEN = "showSettings(); openSettingsPage('apps'); setTimeout(() => MemuSettingsKit.openSub('%s'), 500)"

def seed(data_dir: str, kind: str) -> None:
    from app import gdrive_sync_crypto as crypto
    folder = Path(data_dir) / "memuai" / "sync"
    memu = Path(data_dir) / "memuai"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    store = crypto.SecretStore(folder)
    integ = {"github": {"token": "ghp_" + "x" * 36, "default_repo": "me/notes", "checked": 1700000000, "account": "me"}}
    if kind != "none":
        store.set("google_refresh_token", "1//seeded-refresh-token")
        store.set("google_scopes", " ".join(sorted(OLD if kind == "limited" else FULL)))
        (folder / "settings.json").write_text(json.dumps({"client_id": "123456789012-demo.apps.googleusercontent.com"}), encoding="utf-8")
        (folder / "state.json").write_text(json.dumps({"account": {"email": "alex.demo@gmail.com", "name": "Alex Demo"}}), encoding="utf-8")
        integ["google"] = {"services": ["gmail", "calendar", "drive"] if kind == "limited"
                           else ["gmail", "calendar", "drive", "docs", "sheets", "tasks", "contacts"]}
    memu.mkdir(parents=True, exist_ok=True)
    (memu / "integrations.json").write_text(json.dumps(integ), encoding="utf-8")

def states(seed_kind: str):
    google = OPEN % "int-google"
    out = []
    if seed_kind == "none":
        out += [("google-wizard", google + "; setTimeout(() => document.querySelector('#gi-client-more')?.scrollIntoView({block: 'start'}), 1100)", 2000),
                ("google-wizard-apis", google + "; setTimeout(() => document.querySelector('.gi-api-chips')?.scrollIntoView({block: 'center'}), 1100)", 2000),
                ("google-wizard-agent", google + "; setTimeout(() => document.querySelector('.gi-agent-note')?.scrollIntoView({block: 'center'}), 1100)", 2000),
                ("github-guide", OPEN % "int-github" + "; setTimeout(() => { const d = document.querySelector('#github-guide'); if (d) { d.open = true; d.scrollIntoView({block: 'start'}); } }, 900)", 1800)]
    if seed_kind == "limited":
        out += [("google-limited", google, 2200)]
    if seed_kind == "full":
        out += [("google-check", google + "; setTimeout(() => document.querySelector('[data-gi=\"google-check\"]')?.click(), 1400); setTimeout(() => document.querySelector('[data-gi-row=\"gmail\"]')?.scrollIntoView({block: 'start'}), 3200)", 4200),
                ("google-check-summary", google + "; setTimeout(() => document.querySelector('[data-gi=\"google-check\"]')?.click(), 1400); setTimeout(() => document.querySelector('#gi-check')?.scrollIntoView({block: 'center'}), 3200)", 4200),
                ("confirm-delete-repo", "MemuIntegrations.confirm('github_delete_repo', {repo: 'me/old-project'})", 900),
                ("confirm-gmail-delete", "MemuIntegrations.confirm('gmail_delete', {message_ids: ['18c1', '18c2', '18c3', '18c4', '18c5']})", 900),
                ("confirm-drive-share", "MemuIntegrations.confirm('drive_share', {file_id: 'f9', email: 'anna@example.com', role: 'writer', message: 'Посмотри, пожалуйста'})", 900),
                ("confirm-commit", "MemuIntegrations.confirm('github_commit_files', {repo: 'me/notes', branch: 'main', message: 'docs: update', files: [{path: 'README.md', content: '# Notes'}, {path: 'docs/a.md', content: 'A'}], delete: ['old.txt']})", 900)]
    return out

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--lang", default="ru,en")
    ap.add_argument("--theme", default="dark,light")
    ap.add_argument("--design", default="classic,glass")
    ap.add_argument("--size", default="1366x768")
    ap.add_argument("--seeds", default="none,limited,full")
    ap.add_argument("--states", default="")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    data = tempfile.mkdtemp(prefix="memu-r33-shots-", dir=os.environ.get("R33_SCRATCH") or None)
    os.environ["MEMUAI_DATA_DIR"] = data
    seed(data, "none")
    import httpx
    from playwright.sync_api import sync_playwright
    from round33_mock_apis import Fake33, install
    from ui_lang_scan import Servers
    fake = Fake33()
    install(fake)
    servers = Servers().start()
    w, h = (int(x) for x in a.size.split("x"))
    only = set(filter(None, a.states.split(",")))
    problems: list = []
    try:
        with sync_playwright() as play:
            browser = play.chromium.launch(executable_path=os.environ.get("MEMU_TEST_CHROMIUM", "/usr/bin/chromium"), args=["--no-sandbox"])
            for seed_kind in a.seeds.split(","):
                seed(data, seed_kind)
                from app import gdrive_sync, google_account, integrations
                gdrive_sync._SERVICE = None
                google_account._migrated["done"] = False
                integrations._tokens.clear()
                fake.granted = set(OLD if seed_kind == "limited" else FULL)
                fake.disabled = {"gmail.googleapis.com"} if seed_kind == "full" else set()
                for lang in a.lang.split(","):
                    with httpx.Client(timeout=30, trust_env=False) as client:
                        client.post(servers.pg + "/api/pg/preferences", json={"profile_onboarded": True, "profile_name": "Alex",
                                                                              "language": lang, "updates_enabled": False})
                    for design in a.design.split(","):
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
                            page.evaluate(DESIGN_JS, [design, theme])
                            for name, code, wait in states(seed_kind):
                                if only and name not in only:
                                    continue
                                cur["state"] = name
                                page.evaluate("() => { try { closePanel(); } catch (e) {} }")
                                page.wait_for_timeout(300)
                                page.evaluate("() => { " + code + " }")
                                page.wait_for_timeout(wait)
                                if not name.startswith("confirm"):
                                    res = page.evaluate(CHECK_JS, lang)
                                    for key in ("clip", "cyr"):
                                        for text in res.get(key) or []:
                                            problems.append(f"{lang}/{design}/{theme} [{name}] {key}: {text}")
                                elif lang == "en":
                                    cyr = page.evaluate("() => /[А-Яа-яЁё]/.test(document.querySelector('.gi-confirm h2')?.textContent + document.querySelector('.gi-confirm .row-end')?.textContent)")
                                    if cyr:
                                        problems.append(f"{lang}/{design}/{theme} [{name}] cyr in the card")
                                page.screenshot(path=str(out / f"{design}-{theme}-{lang}-{name}.png"))
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
