"""Round 29 (r29-integrate-ui): the MAIN MemuAI window with every Round-29 feature loaded together.

    PYTHONUTF8=1 python3 tests/round29_integrate_ui_shots.py OUT_DIR --lang ru --design classic --theme dark \
        [--size 1366x728] [--states home,search,...] [--json REPORT]

Real servers (tests/ui_lang_scan.Servers) over a temporary data dir with mock data (chats with markdown tables,
a memuai-chart and a mermaid block, memory facts). For one language × design × theme × window size it opens every
new UI (global search, memory, Settings pages, hotkeys cheat sheet, composer schedule menu and table editor,
streamed table → table + toolbar + image studio, chart/mermaid + export menu, chat export dialog, stuck pause
card, Characters module, panel context menu), takes a screenshot of each and checks automatically:
  * no page errors, console errors or HTTP ≥ 400 responses;
  * overlays (dialogs, menus, palettes) stay inside the window;
  * no clipped text in buttons/tabs/titles of the opened UI (scrollWidth > clientWidth without an ellipsis);
  * in English no Cyrillic in visible text (seeded user content is Russian-free in EN runs).
Exit code 1 on a problem. Screens: <design>-<theme>-<lang>-<W>-<state>.png in OUT_DIR.
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

TABLE_MD = ("| Город | Население, млн | Площадь, км² | Страна |\n|---|---:|---:|---|\n"
            "| Москва | 13,1 | 2561 | Россия |\n| Токио | 14,0 | 2194 | Япония |\n| Берлин | 3,7 | 891 | Германия |\n"
            "| Мадрид | 3,3 | 604 | Испания |\n| Рим | 2,8 | 1285 | Италия |")
TABLE_MD_EN = ("| City | Population, M | Area, km² | Country |\n|---|---:|---:|---|\n"
               "| Moscow | 13.1 | 2561 | Russia |\n| Tokyo | 14.0 | 2194 | Japan |\n| Berlin | 3.7 | 891 | Germany |\n"
               "| Madrid | 3.3 | 604 | Spain |\n| Rome | 2.8 | 1285 | Italy |")
CHART = ('```memuai-chart\n{"type":"bar","title":"Revenue by quarter","labels":["Q1","Q2","Q3","Q4"],'
         '"series":[{"name":"2024","data":[12,15,11,19]},{"name":"2025","data":[14,18,16,22]}],'
         '"y":{"title":"Revenue","unit":"M$"},"x":{"title":"Quarter"}}\n```')
MERMAID = '```mermaid\nflowchart LR\n  A["Request"] --> B{"Valid?"}\n  B -->|yes| C["Process"]\n  B -->|no| D["Reject"]\n```'


def seed_chats(lang: str) -> list[dict]:
    en = lang == "en"
    now = int(time.time() * 1000)
    table = TABLE_MD_EN if en else TABLE_MD
    return [
        {"id": "c_tbl", "title": "Cities and charts" if en else "Города и графики", "created": now - 400000, "updated": now - 10000, "mode": "chat",
         "messages": [
             {"role": "user", "content": "Compare the cities in a table" if en else "Сравни города в таблице", "ts": now - 300},
             {"role": "assistant", "content": ("Here is the comparison:\n\n" if en else "Вот сравнение:\n\n") + table, "model": "demo-gpt-test", "ts": now - 290},
             {"role": "user", "content": "Draw a chart and a flowchart" if en else "Нарисуй график и схему", "ts": now - 200},
             {"role": "assistant", "content": ("Chart:\n\n" if en else "График:\n\n") + CHART + ("\n\nScheme:\n\n" if en else "\n\nСхема:\n\n") + MERMAID,
              "model": "demo-gpt-test", "ts": now - 190}]},
        {"id": "c_plan", "title": "Marathon training plan" if en else "План тренировок к марафону", "created": now - 90000000, "updated": now - 86400000, "mode": "chat",
         "messages": [{"role": "user", "content": "Make a marathon plan" if en else "Составь план к марафону", "ts": now - 9000},
                      {"role": "assistant", "content": "Week 1: easy runs." if en else "Неделя 1: лёгкий бег.", "model": "demo-gpt-test", "ts": now - 8990}]},
    ]


STUCK_CARD = {"id": "stk-demo", "kind": "stuck", "reason": "repeat", "area": "browser",
              "title": "Повторяется одно и то же действие без результата", "title_en": "The same action keeps repeating with no effect",
              "detail": "Клик по «Войти» 4 раза подряд, страница не меняется.", "detail_en": "Clicked “Sign in” 4 times in a row; the page does not change.",
              "steps": [{"label": "click #login", "count": 4}, {"label": "screenshot", "count": 1, "error": "timeout 30s"}],
              "count": 4, "step": 9, "options": ["continue", "hint", "replan", "stop"], "created": 0}

#: Checks of whatever is open now. ``sel`` = the root of the opened UI (or null for the whole page).
CHECK_JS = r"""(sel) => {
  const vw = innerWidth, vh = innerHeight, out = { off: [], clip: [], cyr: [] };
  const roots = sel ? [...document.querySelectorAll(sel)] : [document.body];
  const vis = (n) => { const r = n.getBoundingClientRect(); if (!r.width || !r.height) return null; const cs = getComputedStyle(n);
    if (cs.visibility === 'hidden' || cs.display === 'none' || +cs.opacity === 0) return null; return r; };
  const name = (n) => (n.id ? '#' + n.id : '') + (n.className && typeof n.className === 'string' ? '.' + n.className.trim().split(/\s+/).slice(0, 2).join('.') : '') || n.tagName;
  // overlays inside the window
  document.querySelectorAll('body *').forEach((n) => {
    const cs = getComputedStyle(n); if (cs.position !== 'fixed') return;
    const r = vis(n); if (!r) return;
    if (r.width < 40 || r.height < 20) return;
    if (r.left < -2 || r.top < -2 || r.right > vw + 2 || r.bottom > vh + 2) {
      if (n.closest('[aria-hidden="true"], .toast-container')) return;
      out.off.push(name(n) + ` [${Math.round(r.left)},${Math.round(r.top)} ${Math.round(r.width)}x${Math.round(r.height)}]`);
    }
  });
  for (const root of roots) {
    root.querySelectorAll('button, [role="tab"], h1, h2, h3, h4, label, .sx-title, .mt-btn, a, summary, th').forEach((n) => {
      const r = vis(n); if (!r || r.width < 8) return;
      const cs = getComputedStyle(n);
      if (cs.textOverflow === 'ellipsis' || cs.whiteSpace !== 'nowrap' && n.scrollHeight <= n.clientHeight + 2) { if (cs.textOverflow === 'ellipsis') return; }
      if (!/(hidden|clip)/.test(cs.overflow + cs.overflowX)) return;
      if (n.scrollWidth > n.clientWidth + 2 || n.scrollHeight > n.clientHeight + 3 && cs.whiteSpace === 'nowrap') {
        const txt = (n.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 40);
        if (txt) out.clip.push(name(n) + ' «' + txt + '»');
      }
    });
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) { const t = walker.currentNode, p = t.parentElement;
      if (!p || p.closest('script,style,textarea,.msg-content,.md,code,pre,[data-lang-scan-skip],.mem2-text')) continue;
      if (!/[А-Яа-яЁё]/.test(t.nodeValue)) continue;
      if (!vis(p)) continue;
      const v = t.nodeValue.trim(); if (/^(Русский|RU · Русский|Русский \(RU\))$/.test(v)) continue;
      out.cyr.push(name(p) + ' «' + v.slice(0, 50) + '»'); }
  }
  out.off = [...new Set(out.off)].slice(0, 8); out.clip = [...new Set(out.clip)].slice(0, 12); out.cyr = [...new Set(out.cyr)].slice(0, 12);
  return out;
}"""

DESIGN_JS = """([design, theme]) => { try { setTheme(theme, false); } catch (e) {}
  if (design === 'classic' || design === 'default') delete document.documentElement.dataset.design; else document.documentElement.dataset.design = design; }"""

CLOSE_JS = """() => { try { MemuSearch.close(); } catch (e) {} try { typeof closePanel === 'function' && closePanel(); } catch (e) {}
  document.querySelectorAll('.mt-modal-back, .memu-ctx, .mx-back').forEach((n) => { try { n.querySelector('[data-close], .mt-close, .mx-close')?.click(); } catch (e) {} });
  document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); }"""


def states(lang: str) -> list[tuple[str, str, int, str | None]]:
    """(name, js, wait ms, check selector). JS strings run as a function body; real key presses are tagged KEY:."""
    S = []
    S.append(("home", "", 300, None))
    S.append(("search", "MemuSearch.open('" + ("plan" if lang == "en" else "план") + "')", 1500, ".msearch, #memu-search, [class*='msearch']"))
    S.append(("search-empty", "MemuSearch.close(); MemuSearch.open('')", 900, None))
    S.append(("memory", "MemuMemory.open({tab: 'facts'})", 1200, "#panel"))
    S.append(("set-sync", "showSettings(); openSettingsPage('sync')", 1200, "#panel"))
    S.append(("set-about-safe", "showSettings(); openSettingsPage('about'); setTimeout(() => { const el = [...document.querySelectorAll('#panel .settings-tab-pane.active h3, #panel .settings-tab-pane.active .sx-group-title')].pop(); el && el.scrollIntoView({block: 'start'}); }, 400)", 1300, "#panel"))
    S.append(("set-hotkeys", "showSettings(); openSettingsPage('hotkeys')", 1200, "#panel"))
    S.append(("cheatsheet", "KEY:Control+Slash", 1000, None))
    S.append(("set-memunote", "showSettings(); openSettingsPage('memunote')", 1200, "#panel"))
    S.append(("set-quick-explorer", "showSettings(); openSettingsPage('quick'); setTimeout(() => MemuSettingsKit.openSub('quick-plugin-explorer'), 300)", 1500, "#panel"))
    S.append(("set-chat-sched", "showSettings(); openSettingsPage('chat'); setTimeout(() => document.querySelector('#panel [data-slot=\"schedsend\"], #panel .schedsend-settings, #panel [id*=\"schedsend\"]')?.scrollIntoView({block: 'start'}), 400)", 1300, "#panel"))
    S.append(("set-chat-diagrams", "showSettings(); openSettingsPage('chat'); setTimeout(() => document.getElementById('st-diagrams-mode')?.scrollIntoView({block: 'center'}), 400)", 1300, "#panel"))
    S.append(("set-work-stuck", "showSettings(); openSettingsPage('work'); setTimeout(() => document.getElementById('st-stuck-sensitivity')?.scrollIntoView({block: 'center'}), 400)", 1300, "#panel"))
    S.append(("set-telegram", "showSettings(); openSettingsPage('remote'); setTimeout(() => { const els = [...document.querySelectorAll('#panel .settings-tab-pane.active h3, #panel .settings-tab-pane.active .sx-group-title')]; (els[Math.floor(els.length / 2)] || els[0])?.scrollIntoView({block: 'start'}); }, 600)", 1600, "#panel"))
    S.append(("composer-plus", "openChat('c_plan'); setTimeout(() => document.querySelector('#btn-plus')?.click(), 600)", 1500, None))
    S.append(("table-editor", "MemuTables.openEditor({})", 1200, ".mt-modal"))
    S.append(("sched-menu", "RIGHTCLICK:#btn-send", 1000, None))
    S.append(("table-stream", "openChat('c_tbl'); setTimeout(() => { const t = S.messages[1].content; S.messages.push({role: 'user', content: PG_LANG === 'en' ? 'more' : 'ещё', ts: Date.now()/1000}); S.messages.push({role: 'assistant', content: t.slice(0, Math.floor(t.length * 0.6)), streaming: true, model: 'demo-gpt-test'}); renderMessages(); const b = document.querySelector('#messages').parentElement; b.scrollTop = b.scrollHeight; }, 700)", 1700, "#messages"))
    S.append(("table-final", "const m = S.messages[S.messages.length - 1]; m.content = S.messages[1].content; delete m.streaming; renderMessages(); setTimeout(() => { const h = [...document.querySelectorAll('#messages .mt-host')].pop(); h?.scrollIntoView({block: 'center'}); }, 300)", 1300, "#messages"))
    S.append(("table-hover", "HOVER:#messages .mt-host table", 700, "#messages"))
    S.append(("table-export-menu", "CLICK:#messages .mt-host [data-mt='export']", 800, None))
    S.append(("table-studio", "MemuTables.openStudio([...document.querySelectorAll('#messages .mt-host table')].pop())", 1800, ".mt-modal"))
    S.append(("chart", "openChat('c_tbl'); setTimeout(() => { S.messages.splice(4); renderMessages(); }, 600); setTimeout(() => document.querySelector('#messages .memu-diagram')?.scrollIntoView({block: 'start'}), 1800)", 3000, "#messages"))
    S.append(("chart-export", "HOVERCLICK:#messages .memu-diagram[data-memu-diagram='chart'] [data-md='export']", 900, None))
    S.append(("mermaid", "document.querySelector('#messages .memu-diagram[data-memu-diagram=\"mermaid\"]')?.scrollIntoView({block: 'center'})", 2500, "#messages"))
    S.append(("mermaid-export", "HOVERCLICK:#messages .memu-diagram[data-memu-diagram='mermaid'] [data-md='export']", 900, None))
    S.append(("export-dialog", "MemuExport.open()", 4500, None))
    S.append(("stuck", "MemuStuck.automation('browser', {stuck: Object.assign({}, window.__STUCK, {created: Date.now()})})", 1300, "#memu-stuck-dock"))
    S.append(("stuck-hint", "document.querySelector('#memu-stuck-dock [data-stuck-act=\"hint-open\"]')?.click()", 700, "#memu-stuck-dock"))
    S.append(("stuck-clear", "MemuStuck.automation('browser', {})", 400, None))
    S.append(("panels-ctx", "newChat && newChat(); setTimeout(() => { const c = document.querySelector('.panel-card, [data-panel-id], .mp-card'); if (c) { const r = c.getBoundingClientRect(); c.dispatchEvent(new MouseEvent('contextmenu', {bubbles: true, cancelable: true, clientX: r.left + 40, clientY: r.top + 20, button: 2})); } }, 700)", 1500, None))
    S.append(("characters", "MemuModules.open('characters')", 4500, None))
    S.append(("characters-drawer", "FRAME:characters:M.openDrawer('preset')", 1200, None))
    card = {"spec": "chara_card_v2", "spec_version": "2.0", "data": {
        "name": "Alice" if lang == "en" else "Алиса", "description": "{{char}} is a kind witch who lives in a tower.",
        "first_mes": "Welcome to the tower, {{user}}! *smiles*" if lang == "en" else "Добро пожаловать в башню, {{user}}! *улыбается*",
        "scenario": "A quiet evening.", "tags": ["fantasy"]}}
    S.append(("characters-chat", "FRAME:characters:M.importFiles([new File([" + json.dumps(json.dumps(card)) + "], 'alice.json', {type: 'application/json'})])"
              ".then(() => new Promise((r) => setTimeout(r, 700))).then(() => M.openCharacter(M.S.index[0].id))", 2000, None))
    return S


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--lang", default="ru")
    ap.add_argument("--design", default="classic")
    ap.add_argument("--theme", default="dark")
    ap.add_argument("--size", default="1366x728")
    ap.add_argument("--states", default="")
    ap.add_argument("--json", default="")
    ap.add_argument("--group", default="", help="a = search/memory/settings, b = composer/chat/overlays/modules (each fits in ~60 s)")
    a = ap.parse_args()
    os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memu-r29-ui-"))
    import httpx
    from playwright.sync_api import sync_playwright
    from ui_lang_scan import Servers
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    w, h = (int(x) for x in a.size.split("x"))
    tag = f"{a.design}-{a.theme}-{a.lang}-{w}"
    only = set(filter(None, a.states.split(",")))
    names = [n for n, *_ in states(a.lang)]
    cut, mid_a, mid_b = names.index("composer-plus"), names.index("set-memunote"), names.index("chart")
    only |= set({"a": names[:cut], "b": names[cut:], "a1": names[:mid_a], "a2": names[mid_a:cut],
                 "b1": names[cut:mid_b], "b2": names[mid_b:]}.get(a.group, []))
    servers = Servers().start()
    report: dict = {"tag": tag, "errors": [], "states": {}}
    try:
        with httpx.Client(timeout=30, trust_env=False) as client:
            client.post(servers.pg + "/api/pg/preferences", json={"profile_onboarded": True, "profile_name": "Alex", "language": a.lang,
                                                                  "updates_enabled": False})
            client.post(servers.pg + "/api/pg/chats", json={"chats": seed_chats(a.lang)})
            client.post(servers.pg + "/api/memory/items", json={"text": "Prefers short answers" if a.lang == "en" else "Любит краткие ответы", "category": "preference", "pinned": True})
            client.post(servers.pg + "/api/pg/modules/configure", json={"id": "characters", "enabled": True})
        with sync_playwright() as play:
            browser = play.chromium.launch(executable_path=os.environ.get("MEMU_TEST_CHROMIUM", "/usr/bin/chromium"), args=["--no-sandbox"])
            ctx = browser.new_context(viewport={"width": w, "height": h}, bypass_csp=True)
            ctx.add_init_script(f"try {{ localStorage.setItem('memu_lang', '{a.lang}'); }} catch (_) {{}}")
            page = ctx.new_page()
            errs = report["errors"]
            cur = {"state": "boot"}
            page.on("console", lambda m: m.type == "error" and errs.append(f"[{cur['state']}] console: {m.text[:240]}"))
            page.on("pageerror", lambda e: errs.append(f"[{cur['state']}] pageerror: {str(e)[:300]}"))
            page.on("response", lambda r: r.status >= 400 and errs.append(f"[{cur['state']}] HTTP {r.status} {r.url[:160]}"))
            page.goto(servers.pg + "/")
            page.wait_for_function("document.body.classList.contains('boot-ready')", timeout=90000)
            page.wait_for_timeout(1200)
            page.evaluate("(c) => { window.__STUCK = c; }", STUCK_CARD)
            page.evaluate(DESIGN_JS, [a.design, a.theme])
            page.wait_for_timeout(1200)
            for name, code, wait, sel in states(a.lang):
                if only and name not in only:
                    continue
                cur["state"] = name
                try:
                    if code.startswith("KEY:"):
                        page.keyboard.press(code[4:])
                    elif code.startswith("RIGHTCLICK:"):
                        page.evaluate("() => { const i = document.getElementById('input'); if (i) { i.value = PG_LANG === 'en' ? 'Remind me about the report' : 'Напомни про отчёт'; i.dispatchEvent(new Event('input', {bubbles: true})); } }")
                        page.click(code[11:], button="right", timeout=4000)
                    elif code.startswith("HOVERCLICK:"):
                        target = code[11:]
                        host = target.split(" [data-")[0]
                        page.hover(host, timeout=4000)
                        page.wait_for_timeout(250)
                        page.click(target, timeout=4000)
                    elif code.startswith("HOVER:"):
                        page.hover(code[6:], timeout=4000)
                    elif code.startswith("CLICK:"):
                        page.hover(code[6:].split(" [data-")[0], timeout=4000)
                        page.click(code[6:], timeout=4000)
                    elif code.startswith("FRAME:"):
                        _, mid, js = code.split(":", 2)
                        frame = next((f for f in page.frames if f"/{mid}/" in f.url or f"id={mid}" in f.url or mid in f.url), None)
                        if frame is None:
                            raise RuntimeError("module frame not found: " + ", ".join(f.url[:80] for f in page.frames))
                        frame.evaluate("async () => { window.M = window.MemuCharacters; await (" + js + "); }")
                    elif code:
                        page.evaluate("() => { " + code + " }")
                except Exception as exc:  # noqa: BLE001
                    errs.append(f"[{name}] step failed: {str(exc).splitlines()[0][:200]}")
                page.wait_for_timeout(wait)
                try:
                    res = page.evaluate(CHECK_JS, sel)
                except Exception as exc:  # noqa: BLE001
                    res = {"off": [], "clip": [], "cyr": [], "err": str(exc)[:200]}
                if a.lang != "en":
                    res["cyr"] = []
                report["states"][name] = res
                page.screenshot(path=str(out / f"{tag}-{name}.png"))
                if name not in ("table-hover", "chart", "table-stream", "table-final", "stuck", "stuck-hint", "characters", "characters-drawer"):
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(120)
                if name in ("cheatsheet", "set-sync", "set-about-safe", "set-hotkeys", "set-memunote", "set-quick-explorer", "set-chat-sched",
                            "set-chat-diagrams", "set-work-stuck", "set-telegram", "memory", "search", "search-empty", "table-studio",
                            "export-dialog", "table-editor", "sched-menu", "composer-plus", "panels-ctx", "chart-export", "mermaid-export", "table-export-menu"):
                    page.evaluate(CLOSE_JS)
                    page.wait_for_timeout(200)
            browser.close()
    finally:
        servers.stop()
    problems = 0
    for name, res in report["states"].items():
        for key in ("off", "clip", "cyr"):
            if res.get(key):
                problems += 1
                print(f"{tag} {name} {key}: {res[key]}")
    for e in report["errors"]:
        print(f"{tag} ERROR {e}")
    if a.json:
        Path(a.json).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"done {tag}: {problems} check findings, {len(report['errors'])} errors")
    return 1 if problems or report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
