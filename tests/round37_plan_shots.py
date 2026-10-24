# -*- coding: utf-8 -*-
"""Round 37 (plan): screenshots of the Planning builder — conditions, triggers, options, «Проверить сейчас».

    python tests/round37_plan_shots.py OUT_DIR [--root PATH_TO_A_MEMUAI_COPY] [--langs ru,en]

Real local servers (tests/ui_lang_scan.Servers) in a temporary data folder; the network calls of the new
conditions (weather, place search) are not made — the shown rule uses no weather until «check» is pressed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("out")
ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
ap.add_argument("--langs", default="ru,en")
args = ap.parse_args()
ROOT = Path(args.root).resolve()
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from playwright.sync_api import sync_playwright  # noqa: E402
from ui_lang_scan import Servers  # noqa: E402
import httpx  # noqa: E402

CHROME = os.environ.get("MEMU_TEST_CHROMIUM", "/workspace/work/chromium/chrome")
RULE = {
    "when": [{"type": "daily", "times": ["09:00"], "days": [1, 2, 3, 4, 5]},
             {"type": "monthly", "mode": "nth_weekday", "nth": 2, "weekday": 2, "time": "10:00"},
             {"type": "sun", "event": "sunset", "offset": -30, "lat": 55.93, "lon": 23.31, "place": "Šiauliai"}],
    "only_if": [{"type": "calendar", "mode": "workday", "country": "LT"},
                {"type": "group", "match": "any", "items": [
                    {"type": "wifi", "ssid": "Home", "value": True},
                    {"type": "process", "name": "steam.exe", "running": True, "not": True}]},
                {"type": "last_result", "status": "fail"}],
    "options": {"quiet_from": "23:00", "quiet_to": "07:00", "max_per_day": 3, "retry_count": 2, "retry_minutes": 5},
}


def main() -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    servers = Servers().start()
    http = httpx.Client(timeout=30, trust_env=False)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
            for lang in args.langs.split(","):
                http.post(servers.pg + "/api/pg/preferences", json={
                    "profile_onboarded": True, "profile_name": "Alex", "language": lang, "updates_enabled": False,
                    "ui_motion": "reduced"})
                for theme in ("dark", "light"):
                    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, color_scheme=theme)
                    page = ctx.new_page()
                    errors: list = []
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    page.goto(servers.pg + "/")
                    page.wait_for_selector("body.boot-ready", timeout=90000)
                    page.evaluate(f"document.documentElement.dataset.theme = {json.dumps(theme)}")
                    page.evaluate("(async () => { await showSchedule(true); MemuPlan.openBuilder("
                                  + json.dumps({"id": "shot1", "name": "Демо" if lang == "ru" else "Demo", "kind": "notify",
                                                "prompt": "{{weekday}}: пора", "rule": RULE}) + "); })()")
                    page.wait_for_selector("#pln-builder #pb-if .pln-row", timeout=20000)
                    page.wait_for_timeout(900)
                    page.add_style_tag(content="#pln-summary.pcx-shot-hide { display: none !important; }")
                    page.evaluate("document.querySelector('#pln-summary').classList.add('pcx-shot-hide')")
                    for name, sel in (("when", "#pb-when"), ("if", "#pb-if"), ("until", "#pb-until")):
                        page.evaluate("document.querySelector('#pb-until details')?.setAttribute('open','')")
                        el = page.query_selector(sel)
                        if el:
                            el.scroll_into_view_if_needed()
                            el.screenshot(path=str(out / f"{lang}-{theme}-{name}.png"))
                    btn = page.query_selector("[data-act='pcx-check']")
                    if btn:
                        btn.click()
                        page.wait_for_selector(".pcx-check-box", timeout=20000)
                        page.wait_for_timeout(400)
                        page.query_selector("#pb-if").screenshot(path=str(out / f"{lang}-{theme}-if-checked.png"))
                    page.evaluate("document.querySelector('#pln-summary').classList.remove('pcx-shot-hide')")
                    page.screenshot(path=str(out / f"{lang}-{theme}-full.png"), full_page=False)
                    if errors:
                        print("page errors:", lang, theme, errors)
                    ctx.close()
            browser.close()
    finally:
        http.close()
        servers.stop()


if __name__ == "__main__":
    main()
