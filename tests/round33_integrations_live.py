"""Round 33 (r33-integrations): a LIVE model drives the new GitHub / Gmail / Drive / Tasks tools through the real MemuAI
window; GitHub and Google are the in-memory fakes of tests/round33_mock_apis.py (no real accounts, no traffic to them).

    set -a; . keys.env; set +a
    LIVE_BASE=$AWS9_BASE LIVE_KEY=$AWS9_KEY LIVE_MODEL=gpt-6.1-sol PYTHONUTF8=1 \\
        python3 tests/round33_integrations_live.py OUT_DIR [--only gmail-trash,…]

Real Proxy + playground (tests/ui_lang_scan.Servers) over a temporary data dir with a signed-in Google account (all
seven services, full scopes) and a GitHub token; a Custom OpenAI-compatible account points at the live model. Each
scenario types a request into the chat, approves the confirmation card (recording that it appeared and what it said),
waits for the answer and checks the tools the model called and the effect on the fake APIs. Keys: environment only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

G = "https://www.googleapis.com/auth/"
FULL = {"openid", G + "userinfo.email", "https://mail.google.com/", G + "gmail.settings.basic", G + "calendar", G + "drive",
        G + "documents", G + "spreadsheets", G + "tasks", G + "contacts"}

SCENARIOS = [
    ("gmail-trash", "Удали письмо от Бориса про встречу.",
     [{"gmail_trash"}, {"gmail_delete"}, {"gmail_bulk"}],
     lambda f: "TRASH" in f.messages.get("m2", {}).get("labelIds", ["TRASH"]) or "m2" not in f.messages, None),
    ("gmail-archive", "Отметь письмо Анны про отчёт прочитанным и убери его в архив.",
     [{"gmail_archive"}, {"gmail_modify"}],
     lambda f: "INBOX" not in f.messages["m1"]["labelIds"] and "UNREAD" not in f.messages["m1"]["labelIds"], None),
    ("drive-rename", "Переименуй на Google Диске файл «Заметки.txt» в «Идеи.txt».",
     [{"drive_rename"}], lambda f: f.files.get("f9", {}).get("name") == "Идеи.txt", None),
    ("drive-delete", "Удали с Google Диска файл «Отчёт Q3».",
     [{"drive_trash"}, {"drive_delete_file"}],
     lambda f: "f1" not in f.files or f.files["f1"].get("trashed") is True, None),
    ("github-delete-file", "Удали файл docs/b.md в моём репозитории me/notes.",
     [{"github_delete_file"}], lambda f: "docs/b.md" not in f.repos["me/notes"]["files"], None),
    ("github-create-repo", "Создай на GitHub приватный репозиторий memu-test с README.",
     [{"github_create_repo"}], lambda f: "me/memu-test" in f.repos, None),
    ("github-delete-repo", "Удали репозиторий me/memu-test на GitHub — он больше не нужен.",
     [{"github_delete_repo"}], lambda f: "me/memu-test" not in f.repos, None),
    ("tasks-create", "Добавь в Google Задачи задачу «Позвонить маме» со сроком 2026-10-16.",
     [{"gtasks_create"}], lambda f: any(t.get("title") == "Позвонить маме" for t in f.tasks), None),
    ("gmail-api-disabled", "Что у меня нового в почте?",
     [{"gmail_search"}], None, lambda f: f.disabled.add("gmail.googleapis.com")),
]

def seed(data_dir: str) -> None:
    from app import gdrive_sync_crypto as crypto
    folder = Path(data_dir) / "memuai" / "sync"
    folder.mkdir(parents=True, exist_ok=True)
    store = crypto.SecretStore(folder)
    store.set("google_refresh_token", "1//seeded-refresh-token")
    store.set("google_scopes", " ".join(sorted(FULL)))
    (folder / "settings.json").write_text(json.dumps({"client_id": "123456789012-demo.apps.googleusercontent.com"}), encoding="utf-8")
    (folder / "state.json").write_text(json.dumps({"account": {"email": "alex.demo@gmail.com", "name": "Alex Demo"}}), encoding="utf-8")
    memu = Path(data_dir) / "memuai"
    (memu / "integrations.json").write_text(json.dumps({
        "google": {"services": ["gmail", "calendar", "drive", "docs", "sheets", "tasks", "contacts"]},
        "github": {"token": "ghp_" + "x" * 36, "default_repo": "me/notes", "checked": 1700000000, "account": "me"}}), encoding="utf-8")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--only", default="")
    ap.add_argument("--timeout", type=int, default=300)
    a = ap.parse_args()
    base, key, model = os.environ.get("LIVE_BASE"), os.environ.get("LIVE_KEY"), os.environ.get("LIVE_MODEL")
    if not (base and key and model):
        print("set LIVE_BASE, LIVE_KEY, LIVE_MODEL")
        return 2
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    data = tempfile.mkdtemp(prefix="memu-r33-live-", dir=os.environ.get("R33_SCRATCH") or None)
    os.environ["MEMUAI_DATA_DIR"] = data
    seed(data)
    import httpx
    from playwright.sync_api import sync_playwright
    from round33_mock_apis import Fake33, install
    from ui_lang_scan import Servers
    fake = Fake33()
    fake.github_token = "ghp_" + "x" * 36
    fake.granted = set(FULL)
    install(fake)
    servers = Servers().start()
    only = set(filter(None, a.only.split(",")))
    report = []
    try:
        with httpx.Client(timeout=60, trust_env=False) as client:
            client.post(servers.proxy + "/api/admin/accounts", json={"provider": "custom", "label": "live",
                        "fields": {"name": "Live", "base_url": base, "api_key": key, "models": model}})
            models = client.get(servers.proxy + "/v1/models").json().get("data") or []
            model_id = next((m["id"] for m in models if m["id"].lower().endswith(model.lower()) and "live" in m["id"].lower()), "")
            client.post(servers.pg + "/api/pg/preferences", json={"profile_onboarded": True, "profile_name": "Alex",
                                                                  "language": "ru", "updates_enabled": False})
        print("model id:", model_id)
        with sync_playwright() as play:
            browser = play.chromium.launch(executable_path=os.environ.get("MEMU_TEST_CHROMIUM", "/usr/bin/chromium"),
                                           args=["--no-sandbox"])
            ctx = browser.new_context(viewport={"width": 1366, "height": 860}, bypass_csp=True)
            ctx.add_init_script(f"try {{ localStorage.setItem('memu_lang', 'ru'); localStorage.setItem('memu_model', {json.dumps(model_id)}); }} catch (_) {{}}")
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)[:300]))
            page.goto(servers.pg + "/")
            page.wait_for_function("document.body.classList.contains('boot-ready')", timeout=90000)
            page.wait_for_timeout(2500)
            for name, prompt, expected, effect, setup in SCENARIOS:
                if only and name not in only:
                    continue
                if setup:
                    setup(fake)
                before = len(fake.calls)
                page.evaluate("() => { try { newChat(false); } catch (e) {} }")
                page.wait_for_timeout(600)
                page.fill("#input", prompt)
                page.keyboard.press("Enter")
                confirmed = []
                started = time.time()
                last_len, stable = -1, 0
                while time.time() - started < a.timeout:
                    page.wait_for_timeout(1500)
                    card = page.evaluate("""() => { const b = document.querySelector('#panel-back.visible #gi-confirm-yes');
                      return b && b.getClientRects().length ? document.querySelector('.gi-confirm').innerText : ''; }""")
                    if card:
                        confirmed.append(card[:500])
                        page.wait_for_timeout(400)
                        page.screenshot(path=str(out / f"{name}-confirm-{len(confirmed)}.png"))
                        page.click("#panel-back.visible #gi-confirm-yes")
                        continue
                    state = page.evaluate("""() => { const c = currentChat();
                      const ms = ((c && c.messages) || []).filter((x) => x && !x.internal); const m = ms[ms.length - 1];
                      return { role: m && m.role, len: m ? String(m.content || '').length : 0,
                               busy: !!currentChatBusy() || !!generating || !!(m && m.streaming) }; }""")
                    if state["role"] == "assistant" and state["len"] > 0 and not state["busy"]:
                        stable = stable + 1 if state["len"] == last_len else 0
                        last_len = state["len"]
                        if stable >= 2:
                            break
                final = page.evaluate("""() => { const c = currentChat(); const ms = c.messages || [];
                  const answers = ms.filter(m => m.role === 'assistant');
                  return { text: String((answers[answers.length - 1] || {}).content || '').slice(0, 1200),
                           tools: answers.flatMap(m => (m.toolHistory || []).filter(x => x.tool).map(x => [x.tool, x.ok, (x.error || '').slice(0, 300)])) }; }""")
                page.screenshot(path=str(out / f"{name}.png"))
                called = {t[0] for t in final["tools"]}
                ok = any(group <= called for group in expected)
                if effect is not None:
                    ok = ok and bool(effect(fake))
                if name == "gmail-api-disabled":
                    ok = ok and "console" in final["text"] and "gmail.googleapis.com" in final["text"]
                    fake.disabled.discard("gmail.googleapis.com")
                if name == "github-delete-repo":
                    ok = ok and bool(confirmed) and "навсегда" in confirmed[-1].lower()
                row = {"scenario": name, "ok": ok, "called": final["tools"], "confirm_cards": [c[:200] for c in confirmed],
                       "api_hits": [c[0] + " " + c[1][-70:] for c in fake.calls[before:]][:16], "answer": final["text"][:700],
                       "seconds": round(time.time() - started)}
                report.append(row)
                print(json.dumps(row, ensure_ascii=False)[:2500], flush=True)
            if errors:
                print("page errors:", errors[:5])
            browser.close()
    finally:
        servers.stop()
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    passed = sum(1 for r in report if r["ok"])
    print(f"passed {passed}/{len(report)}")
    return 0 if passed == len(report) else 1

if __name__ == "__main__":
    raise SystemExit(main())
