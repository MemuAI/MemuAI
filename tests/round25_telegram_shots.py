"""Screenshots of the Telegram Mini App in every design (Round 25).

Usage: python3 tests/round25_telegram_shots.py [design …]
Runs the Mini App server on a free local port with a temporary data dir and a
signed initData in the URL hash (the built-in bridge reads it), then captures
the chats, panels and settings tabs in dark and light at phone size into
/workspace/work/shots/r25/telegram/.
"""
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-shots-")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import pgdata, tg_webapp  # noqa: E402

TOKEN = "123456:SHOT-token_for-mini-app"
USER = 4242
OUT = Path("/workspace/work/shots/r25/telegram")


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def serve(port):
    import uvicorn
    from app.tgapp_api import create_tgapp
    server = uvicorn.Server(uvicorn.Config(create_tgapp(), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            return server
        time.sleep(0.05)
    raise RuntimeError("server did not start")


def seed_panel():
    from tests.round24_telegram_test import WEATHER, make_panel
    make_panel("weather", dict(WEATHER))
    make_panel("countdown", {"target": "2030-01-01", "target_label": "01.01.2030", "days_left": 9})


def main(designs):
    OUT.mkdir(parents=True, exist_ok=True)
    pgdata.save_preferences({"telegram_bot_token": TOKEN, "telegram_chat_id": str(USER), "language": "ru"})
    tg_webapp.save_config({"enabled": True, "mode": "cloudflare"})
    seed_panel()
    port = free_port()
    server = serve(port)
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as play:
            browser = play.chromium.launch()
            for design in designs:
                for scheme in ("dark", "light"):
                    pgdata.save_preferences({"ui_design": design, "theme": scheme})
                    init = tg_webapp.sign_init_data({"auth_date": int(time.time()), "query_id": "AAH-shot",
                                                     "user": {"id": USER, "first_name": "Yono"}}, TOKEN)
                    theme = {"bg_color": "#17212b" if scheme == "dark" else "#ffffff"}
                    hash_part = urllib.parse.urlencode({"tgWebAppData": init, "tgWebAppVersion": "7.10",
                                                        "tgWebAppPlatform": "android",
                                                        "tgWebAppThemeParams": json.dumps(theme)})
                    page = browser.new_page(viewport={"width": 390, "height": 760}, device_scale_factor=2,
                                            color_scheme=scheme)
                    page.goto(f"http://127.0.0.1:{port}/tgapp/#{hash_part}")
                    page.wait_for_selector("#app:not([hidden]) .tab", timeout=15000)
                    page.wait_for_timeout(500)
                    for tab in ("chats", "panels", "settings"):
                        button = page.locator(f'.tab[data-tab="{tab}"]')
                        if button.count():
                            button.first.click()
                            page.wait_for_timeout(700)
                        page.screenshot(path=str(OUT / f"{design}_{scheme}_{tab}.png"))
                    page.close()
            browser.close()
    finally:
        server.should_exit = True
    print("saved to", OUT)


if __name__ == "__main__":
    main(sys.argv[1:] or ["soft", "terminal", "brutal"])
