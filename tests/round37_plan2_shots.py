# -*- coding: utf-8 -*-
"""Round 37 (plan-2): screenshots — the scenario gallery, the weather trigger, the new calendar modes and
a switched-off condition in the Planning builder.

    python tests/round37_plan2_shots.py OUT_DIR [--langs ru,en]

Real local servers (tests/ui_lang_scan.Servers) in a temporary data folder; nothing here calls the network.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("out")
ap.add_argument("--langs", default="ru,en")
args = ap.parse_args()
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from playwright.sync_api import sync_playwright  # noqa: E402
from ui_lang_scan import Servers  # noqa: E402
import httpx  # noqa: E402

CHROME = os.environ.get("MEMU_TEST_CHROMIUM", "/workspace/work/chromium/chrome")
MOSCOW = {"lat": 55.75, "lon": 37.62, "place": "Москва"}
RULE = {
    "when": [{"type": "watch", "source": "weather", "metric": "temp", "op": "lt", "value": 0, "cooldown": 1440, **MOSCOW}],
    "only_if": [{"type": "calendar", "mode": "last_workday_week", "country": "RU"},
                {"type": "calendar", "mode": "short_day", "country": "RU", "off": True}],
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
                    page.evaluate("(async () => { await showSchedule(true); })()")
                    page.wait_for_timeout(1200)
                    page.evaluate("localStorage.setItem('memuai.plan.place.v1', JSON.stringify({lat: 55.75, lon: 37.62, place: 'Москва'}))")
                    page.evaluate("document.querySelector(\"[data-act='new']\").click()")
                    page.wait_for_timeout(400)
                    page.screenshot(path=str(out / f"{lang}-{theme}-new-menu.png"))
                    page.keyboard.press("Escape")
                    page.evaluate("MemuPlanTemplates.open([], () => {})")
                    page.wait_for_selector(".ptg-card", timeout=10000)
                    page.wait_for_timeout(300)
                    page.screenshot(path=str(out / f"{lang}-{theme}-gallery.png"))
                    page.click(".ptg-cats [data-cat='weather']")
                    page.wait_for_timeout(200)
                    page.query_selector(".ptg-box").screenshot(path=str(out / f"{lang}-{theme}-gallery-weather.png"))
                    page.keyboard.press("Escape")
                    page.evaluate("MemuPlan.openBuilder(" + json.dumps({"id": "shot2", "name": "Заморозки" if lang == "ru" else "Frost",
                                                                       "kind": "notify", "prompt": "{{trigger.title}}", "rule": RULE}) + ")")
                    page.wait_for_selector("#pln-builder #pb-if .pln-row", timeout=20000)
                    page.wait_for_timeout(700)
                    page.add_style_tag(content="#pln-summary { display: none !important; }")
                    for name, sel in (("when", "#pb-when"), ("if", "#pb-if")):
                        el = page.query_selector(sel)
                        if el:
                            el.scroll_into_view_if_needed()
                            el.screenshot(path=str(out / f"{lang}-{theme}-{name}.png"))
                    if errors:
                        print("page errors:", lang, theme, errors)
                    ctx.close()
            browser.close()
    finally:
        http.close()
        servers.stop()


if __name__ == "__main__":
    main()
