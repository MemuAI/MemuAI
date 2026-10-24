"""Round 25 (b5): Home panels in every design x light/dark on the REAL UI fixture, with long titles,
long feed rows and empty/failed/setup states. Run: python tests/round25_panels_shots.py [design ...]
-> /workspace/work/shots/r25/b5-panels/*.png. Prints overflowing elements of every card."""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = Path("/workspace/work/shots/r25/b5-panels")
LONG = "Очень длинное название панели, которое никак не помещается в одну строку карточки"


def snapshot():
    from tests.round24_designs_shots import panels_snapshot
    from tests.panels_browser_test import panel
    snap = panels_snapshot()
    snap["panels"][0]["title"] = LONG
    snap["panels"][0]["spec"]["blocks"][0]["text"] = LONG
    snap["panels"] += [
        panel("p_failed0001", "custom", "Курс биткоина и эфира по данным бирж", [], {}, ["home"], status="failed", size="s",
              wall={"status": "blocked:source-down", "message": "HTTP 503 Service Unavailable " * 4}),
        panel("p_setup00001", "weather", "Погода", [], {}, ["home"], status="setup", size="s",
              wall={"status": "needs_location:weather", "message": ""}),
        panel("p_rss0000001", "rss", "RSS", [{"type": "title", "text": "Хабр"},
              {"type": "list", "items": "{{items}}", "max": 6}],
              {"items": [{"title": "Слово" * 40, "url": "https://habr.com/x", "subtitle": "08.10 12:00"},
                         {"title": "https://example.org/" + "a" * 120, "url": "https://example.org/"}]},
              ["home"], size="s", source={"type": "rss", "params": {}}),
    ]
    return snap


def main(designs):
    from playwright.sync_api import sync_playwright
    from tests import round24_designs_shots as r24
    r24.panels_snapshot_orig = r24.panels_snapshot
    data = snapshot()
    r24.panels_snapshot = lambda: data
    OUT.mkdir(parents=True, exist_ok=True)
    fixture = r24.build(Path(tempfile.mkdtemp(prefix="memu-r25p-")))
    report = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        for design in designs:
            for theme in ("light", "dark"):
                page = browser.new_page(viewport={"width": 1360, "height": 1700})
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.add_init_script(f"""try {{ localStorage.setItem('memu_theme', '{theme}');
                  localStorage.setItem('memu_design', '{design}');
                  localStorage.setItem('qa_prefs', JSON.stringify({{theme: '{theme}', ui_design: '{design}', ui_motion: 'reduced'}})); }} catch (_) {{}}""")
                page.goto(fixture.as_uri(), wait_until="domcontentloaded")
                page.wait_for_function("document.body.classList.contains('boot-ready')", timeout=90000)
                page.evaluate(f"setTheme && setTheme('{theme}', false)")
                page.evaluate(f"document.documentElement.dataset.design = '{design}'")
                page.wait_for_selector("#panels-home .mp-card", timeout=10000)
                time.sleep(0.8)
                tag = f"{design}-{theme}"
                page.screenshot(path=str(OUT / f"{tag}-home.png"))
                home = page.locator("#panels-home")
                home.scroll_into_view_if_needed()
                home.screenshot(path=str(OUT / f"{tag}-panels.png"))
                report[tag] = {"errors": errors, "overflow": page.evaluate("""() => [...document.querySelectorAll('#panels-home .mp-card, #group-panels .mp-card')]
                    .flatMap(card => [...card.querySelectorAll('*')].filter(el => {
                      const c = card.getBoundingClientRect(), r = el.getBoundingClientRect();
                      return r.width && (r.right > c.right + 1 || r.left < c.left - 1);
                    }).map(el => card.dataset.panelId + ' ' + el.tagName + '.' + el.className))""")}
                page.close()
        browser.close()
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:] or ["classic", "glass", "soft", "terminal", "brutal"])
