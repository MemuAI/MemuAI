"""Round 30 (r30-design): headless screenshot matrix of the main MemuAI window — search, Planning (incl. the
«New task» menu, «add a trigger» menu, the time picker), Settings → Hotkeys / Panels / Appearance, home and a chat —
for every design × dark/light in one server boot per (language, window size). Not a unit test (run by hand).

    python tests/round30_design_shots.py OUT --lang ru --size 1366x768 [--designs classic,glass] [--themes dark,light] [--states a,b] [--js '...']
        [--prefs '{"ui_glass": "strong", "surface_dark": "slate", "surface_light": "slate", "ui_density": "compact"}']
Writes OUT/<design>-<theme>-<lang>-<w>-<state>.png, OUT/report-<design>-<theme>-<lang>-<w>.json per combo
(off = fixed layer outside the window, clip = cut text, bars = a scrollbar for 1–6px of overflow or a sideways
scrollbar) and OUT/errors-*.json (console / page errors, HTTP ≥ 400). LOOK at the pictures: the checks only
catch the mechanical part. --js runs after boot (e.g. "document.documentElement.dataset.glass='strong'"),
--tag prefixes the file names, --noshot only checks. See README_FOR_AI.md «Раунд 30: дизайн».
"""
from __future__ import annotations
import argparse, json, os, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from round27_ui_lang_test import _chromium  # noqa: E402
from round29_integrate_ui_shots import CHECK_JS, DESIGN_JS, seed_chats  # noqa

DESIGNS = ["classic", "aurora", "focus", "grid", "glass", "soft", "terminal", "brutal"]

BARS_JS = r"""(sel) => {
  const out = [];
  const roots = sel ? [...document.querySelectorAll(sel)] : [document.body];
  const name = (n) => (n.id ? '#' + n.id : '') + (n.className && typeof n.className === 'string' ? '.' + n.className.trim().split(/\s+/).slice(0, 2).join('.') : '') || n.tagName;
  for (const root of roots) for (const n of [root, ...root.querySelectorAll('*')]) {
    const cs = getComputedStyle(n); const r = n.getBoundingClientRect();
    if (!r.width || !r.height || cs.display === 'none' || cs.visibility === 'hidden') continue;
    const sb = cs.scrollbarWidth === 'none';
    const y = /(auto|scroll)/.test(cs.overflowY), x = /(auto|scroll)/.test(cs.overflowX);
    const dy = n.scrollHeight - n.clientHeight, dx = n.scrollWidth - n.clientWidth;
    if (y && dy > 0 && dy <= 6 && !sb) out.push('y+' + dy + ' ' + name(n));
    if (x && dx > 1 && !sb && n.offsetHeight - n.clientHeight > 0) out.push('x+' + dx + ' ' + name(n));
  }
  return [...new Set(out)].slice(0, 10);
}"""

RESET_JS = """() => { try { MemuSearch.close(); } catch (e) {} document.querySelectorAll('.pln-pop, .msx-more-pop').forEach((n) => n.remove());
  try { typeof closePanel === 'function' && closePanel(); } catch (e) {} }"""

def states(lang):
    en = lang == "en"
    S = []
    S.append(("home", "closePanel && closePanel(); newChat && newChat()", 900, None))
    S.append(("chat", "closePanel && closePanel(); const c=document.querySelector('.chat-item[data-chat]'); c && openChat(c.dataset.chat)", 1200, None))
    S.append(("search", f"MemuSearch.open('{'plan' if en else 'план'}')", 1300, None))
    S.append(("search-down", "KEY:ArrowDown", 400, None))
    S.append(("search-empty", "MemuSearch.close(); MemuSearch.open('')", 800, None))
    S.append(("search-none", "MemuSearch.close(); MemuSearch.open('zzqqxx')", 1000, None))
    S.append(("plan", "MemuSearch.close(); showSchedule(true)", 1300, "#panel"))
    S.append(("plan-help", "CLICK:#pln-root [data-act='help-show']", 800, "#panel"))
    S.append(("plan-new", "CLICK:#pln-root [data-act='new']", 600, "#panel"))
    S.append(("plan-builder", "document.querySelector('.pln-pop')?.remove(); MemuPlan.openBuilder({id:'x1', name:'', kind:'chat'})", 1300, "#panel"))
    S.append(("plan-builder-do", "document.querySelector('#pb-do')?.scrollIntoView({block:'start'})", 500, "#panel"))
    S.append(("plan-addwhen", "CLICK:#pln-builder [data-act='add-when']", 600, "#panel"))
    S.append(("plan-time", "document.querySelector('.pln-pop')?.remove(); document.querySelector('#pb-when input[data-time]')?.focus()", 600, "#panel"))
    S.append(("set-hotkeys", "KEY:Escape;showSettings(); openSettingsPage('hotkeys')", 1100, "#panel"))
    S.append(("set-panels", "showSettings(); openSettingsPage('panels')", 1100, "#panel"))
    S.append(("set-appearance", "showSettings(); openSettingsPage('appearance')", 1100, "#panel"))
    S.append(("set-ap-shape", "document.querySelector('#appearance-shape-section')?.scrollIntoView({block:'start'})", 500, "#panel"))
    S.append(("set-ap-motion", "document.querySelector('#appearance-motion-section')?.scrollIntoView({block:'start'})", 500, "#panel"))
    return S

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("out"); ap.add_argument("--lang", default="ru"); ap.add_argument("--size", default="1366x768")
    ap.add_argument("--designs", default=",".join(DESIGNS)); ap.add_argument("--themes", default="dark,light"); ap.add_argument("--states", default="")
    ap.add_argument("--js", default=""); ap.add_argument("--tag", default=""); ap.add_argument("--noshot", action="store_true")
    ap.add_argument("--prefs", default="", help='extra preferences as JSON, e.g. {"ui_glass": "strong", "surface_dark": "slate"}')
    a = ap.parse_args()
    os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="memu-r30m-"))
    import httpx
    from playwright.sync_api import sync_playwright
    from ui_lang_scan import Servers
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    w, h = (int(x) for x in a.size.split("x"))
    only = set(filter(None, a.states.split(",")))
    en = a.lang == "en"
    servers = Servers().start(); report = {"errors": [], "states": {}}
    try:
        with httpx.Client(timeout=30, trust_env=False) as c:
            c.post(servers.pg + "/api/pg/preferences", json={"profile_onboarded": True, "profile_name": "Alex", "language": a.lang, "updates_enabled": False,
                                                            **(json.loads(a.prefs) if a.prefs else {})})
            c.post(servers.pg + "/api/pg/chats", json={"chats": seed_chats(a.lang)})
            for i, (nm, k) in enumerate([("Morning digest" if en else "Утренняя сводка", "chat"), ("Gaming break" if en else "Перерыв в игре", "notify")]):
                c.post(servers.pg + "/api/plan/save", json={"lang": a.lang, "task": {"id": f"t{i}abc", "name": nm, "kind": k, "prompt": "x", "enabled": True,
                    "rule": {"when": [{"type": "daily", "times": ["09:00"], "days": [1, 2, 3, 4, 5]}]}}})
        with sync_playwright() as play:
            b = play.chromium.launch(executable_path=_chromium() or None, args=["--no-sandbox"])
            ctx = b.new_context(viewport={"width": w, "height": h}, bypass_csp=True)
            ctx.add_init_script(f"try {{ localStorage.setItem('memu_lang', '{a.lang}'); }} catch (_) {{}}")
            page = ctx.new_page(); errs = report["errors"]; cur = {"s": "boot"}
            page.on("console", lambda m: m.type == "error" and errs.append(f"[{cur['s']}] console: {m.text[:240]}"))
            page.on("pageerror", lambda e: errs.append(f"[{cur['s']}] pageerror: {str(e)[:300]}"))
            page.on("response", lambda r: r.status >= 400 and errs.append(f"[{cur['s']}] HTTP {r.status} {r.url[:160]}"))
            page.goto(servers.pg + "/")
            page.wait_for_function("document.body.classList.contains('boot-ready')", timeout=90000)
            page.wait_for_timeout(800)
            if a.js: page.evaluate("() => { " + a.js + " }"); page.wait_for_timeout(500)
            for design in a.designs.split(","):
                for theme in a.themes.split(","):
                    tag = f"{a.tag}{design}-{theme}-{a.lang}-{w}"
                    page.evaluate(RESET_JS); page.evaluate(DESIGN_JS, [design, theme]); page.wait_for_timeout(1500)
                    for name, code, wait, sel in states(a.lang):
                        if only and name not in only: continue
                        cur["s"] = f"{tag}-{name}"
                        try:
                            steps = code.split(";", 1) if code.startswith("KEY:") and ";" in code else [code]
                            for st in steps:
                                if st.startswith("KEY:"): page.keyboard.press(st[4:])
                                elif st.startswith("CLICK:"): page.click(st[6:], timeout=4000)
                                elif st: page.evaluate("() => { " + st + " }")
                        except Exception as exc: errs.append(f"[{cur['s']}] step failed: {str(exc).splitlines()[0][:200]}")
                        page.wait_for_timeout(wait)
                        try: res = page.evaluate(CHECK_JS, sel); res["bars"] = page.evaluate(BARS_JS, sel)
                        except Exception as exc: res = {"err": str(exc)[:200]}
                        if not en: res["cyr"] = []
                        report["states"][cur["s"]] = {k: v for k, v in res.items() if v}
                        if not a.noshot: page.screenshot(path=str(out / f"{tag}-{name}.png"))

                    (out / f"report-{tag}.json").write_text(json.dumps({k: v for k, v in report["states"].items() if k.startswith(tag + "-")}, ensure_ascii=False, indent=1), encoding="utf-8")
            b.close()
    finally:
        servers.stop()
    (out / f"errors-{a.tag}{a.lang}-{w}.json").write_text(json.dumps(report["errors"], ensure_ascii=False, indent=1), encoding="utf-8")
    bad = {k: v for k, v in report["states"].items() if v}
    print(f"{len(report['states'])} states, {len(bad)} flagged, {len(report['errors'])} errors")

if __name__ == "__main__":
    main()
