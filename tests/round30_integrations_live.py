"""Round 30 (r30-integrations): a LIVE model calls the Gmail / Calendar / Drive / Notion tools through the real MemuAI
window — the external APIs are the in-memory fakes of tests/round30_mock_apis.py (no Google/Notion traffic).

    LIVE_BASE=https://…/v1 LIVE_KEY=… LIVE_MODEL=… PYTHONUTF8=1 python3 tests/round30_integrations_live.py OUT_DIR [--only gmail,…]

Real Proxy + playground (tests/ui_lang_scan.Servers) over a temporary data dir with a connected Google account and a
Notion token; a Custom OpenAI-compatible account points at the live model. Each scenario types a request into the
chat, auto-approves the write confirmation card (and records that it appeared), waits for the answer and checks which
tools the model called (assistant.toolHistory) and what reached the fake APIs. Keys come from the environment only.
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

SCENARIOS = [
    ("gmail", "Найди в моей почте письмо от Анны про отчёт и коротко перескажи, что она пишет.", {"gmail_search"}, None),
    ("gmail-send", "Ответь Анне (anna@example.com) письмом: тема «Спасибо», текст «Спасибо, посмотрю отчёт сегодня». Отправь.",
     {"gmail_send"}, "sent"),
    ("calendar", "Что у меня в календаре на ближайшую неделю?", {"calendar_list_events"}, None),
    ("calendar-create", "Создай в календаре событие «Обед с Борисом» 2026-10-16 с 13:00 до 14:00.", {"calendar_create_event"}, "event"),
    ("drive", "Найди на Google Диске файл «Отчёт Q3» и скажи, что в нём написано.", {"drive_list_files", "drive_read_file"}, None),
    ("notion", "Что у меня в Notion на странице «Планы на неделю»? Прочитай её.", {"notion_search", "notion_read_page"}, None),
    ("notion-append", "Добавь в Notion на страницу «Планы на неделю» пункт: «- [ ] Отправить отчёт Анне».", {"notion_append"}, "appended"),
]

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--only", default="")
    ap.add_argument("--timeout", type=int, default=240)
    a = ap.parse_args()
    base, key, model = os.environ.get("LIVE_BASE"), os.environ.get("LIVE_KEY"), os.environ.get("LIVE_MODEL")
    if not (base and key and model):
        print("set LIVE_BASE, LIVE_KEY, LIVE_MODEL")
        return 2
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    data = tempfile.mkdtemp(prefix="memu-r30-live-")
    os.environ["MEMUAI_DATA_DIR"] = data
    from round30_integrations_shots import seed_connected
    seed_connected(data, drive=True)
    import httpx
    from playwright.sync_api import sync_playwright
    from round30_mock_apis import FakeApis, install
    from ui_lang_scan import Servers
    fake = FakeApis()
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
            browser = play.chromium.launch(executable_path=os.environ.get("MEMU_TEST_CHROMIUM", "/workspace/work/chromium/chrome"),
                                           args=["--no-sandbox"])
            ctx = browser.new_context(viewport={"width": 1366, "height": 860}, bypass_csp=True)
            ctx.add_init_script(f"try {{ localStorage.setItem('memu_lang', 'ru'); localStorage.setItem('memu_model', {json.dumps(model_id)}); }} catch (_) {{}}")
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)[:300]))
            page.goto(servers.pg + "/")
            page.wait_for_function("document.body.classList.contains('boot-ready')", timeout=90000)
            page.wait_for_timeout(1500)
            for name, prompt, expected, effect in SCENARIOS:
                if only and name not in only:
                    continue
                before = len(fake.calls)
                page.evaluate("() => { try { newChat(false); } catch (e) {} }")
                page.wait_for_timeout(600)
                page.fill("#input", prompt)
                page.keyboard.press("Enter")
                confirmed = []
                started = time.time()
                last_len = -1
                stable = 0
                while time.time() - started < a.timeout:
                    page.wait_for_timeout(1500)
                    card = page.evaluate("""() => { const b = document.querySelector('#panel-back.visible #gi-confirm-yes');
                      return b && b.getClientRects().length ? document.querySelector('.gi-confirm').innerText : ''; }""")
                    if card:
                        confirmed.append(card[:400])
                        page.wait_for_timeout(400)
                        page.screenshot(path=str(out / f"{name}-confirm.png"))
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
                  return { text: String((answers[answers.length - 1] || {}).content || '').slice(0, 900),
                           tools: answers.flatMap(m => (m.toolHistory || []).filter(x => x.tool).map(x => [x.tool, x.ok, (x.error || '').slice(0, 200)])) }; }""")
                page.screenshot(path=str(out / f"{name}.png"))
                called = {t[0] for t in final["tools"]}
                hits = [c[1] for c in fake.calls[before:]]
                ok = expected <= called
                if effect == "sent":
                    ok = ok and bool(fake.sent) and bool(confirmed)
                elif effect == "event":
                    ok = ok and any(e.get("summary", "").startswith("Обед") for e in fake.events) and bool(confirmed)
                elif effect == "appended":
                    ok = ok and bool(fake.appended) and bool(confirmed)
                row = {"scenario": name, "ok": ok, "called": final["tools"], "confirm_cards": len(confirmed),
                       "api_hits": hits[:12], "answer": final["text"][:500], "seconds": round(time.time() - started)}
                report.append(row)
                print(json.dumps(row, ensure_ascii=False)[:1500])
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
