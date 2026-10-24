"""Round 37: screenshots of MemuMusic in the Telegram Mini App (phone 390×844, dark and light Telegram themes).

    MEMUAI_DATA_DIR=/tmp/x python tests/round37_music_tg_shots.py OUT_DIR [--themes dark,light] [--lang ru|en]

Offline: the real Mini App server (create_tgapp) on a free port, a seeded library (round37_music_tg_seed),
this process plays the MemuAI window (fake_window) so the computer's player shows in «Пульт».
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="r37-music-shots-")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

TOKEN = "123456:TEST-token_for-mini-app"
USER = 4242
THEMES = {
    "dark": {"bg_color": "#17212b", "text_color": "#f5f5f5", "hint_color": "#708499", "button_color": "#5288c1",
             "button_text_color": "#ffffff", "secondary_bg_color": "#232e3c"},
    "light": {"bg_color": "#ffffff", "text_color": "#000000", "hint_color": "#999999", "button_color": "#3390ec",
              "button_text_color": "#ffffff", "secondary_bg_color": "#f1f1f4"},
}


def chromium() -> str:
    for path in (os.environ.get("MEMU_TEST_CHROMIUM", ""), "/workspace/work/chromium/chrome", "/usr/bin/chromium"):
        if path and Path(path).exists():
            return path
    return ""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Server:
    """create_tgapp() on a free port in a thread, with the seeded library and the fake window."""

    def __init__(self, lang: str = "ru"):
        self.lang = lang

    def start(self):
        import uvicorn
        import round37_music_tg_seed as seed
        from app import tg_webapp
        from app.pgdata import save_preferences
        save_preferences({"telegram_bot_token": TOKEN, "telegram_chat_id": str(USER), "language": self.lang,
                          "memes": [], "chat_memes": {}})
        tg_webapp.save_config({"enabled": True, "mode": "cloudflare"})
        self.lib, self.ids, self.playlist = seed.seed()
        self.patches = seed.fake_window()
        for patch in self.patches:
            patch.start()
        from app.tgapp_api import create_tgapp
        self.port = free_port()
        self.server = uvicorn.Server(uvicorn.Config(create_tgapp(), host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                break
            time.sleep(0.05)
        self.url = f"http://127.0.0.1:{self.port}"
        return self

    def stop(self):
        self.server.should_exit = True
        self.thread.join(5)
        for patch in self.patches:
            patch.stop()


def open_page(browser, server, theme: str, size=(390, 844), lang="ru"):
    from app import tg_webapp
    init = tg_webapp.sign_init_data({"auth_date": int(time.time()), "query_id": "AAH-r37",
                                     "user": {"id": USER, "first_name": "Yono", "language_code": lang}}, TOKEN)
    frag = urllib.parse.urlencode({"tgWebAppData": init, "tgWebAppVersion": "8.0", "tgWebAppPlatform": "ios",
                                   "tgWebAppThemeParams": json.dumps(THEMES[theme])})
    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]}, is_mobile=True, has_touch=True,
                              device_scale_factor=2, color_scheme=theme)
    page = ctx.new_page()
    errors: list = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{server.url}/tgapp/?tab=music#{frag}")
    page.wait_for_selector("#app:not([hidden]) .tab", timeout=20000)
    page.wait_for_selector(".mm-hello, .mm-empty", timeout=10000)
    page.wait_for_timeout(700)
    return ctx, page, errors


def shots(out: Path, themes, lang="ru"):
    from playwright.sync_api import sync_playwright
    import round37_music_tg_seed as seed
    out.mkdir(parents=True, exist_ok=True)
    server = Server(lang).start()
    errors_all = []
    try:
        with sync_playwright() as play:
            browser = play.chromium.launch(executable_path=chromium(), args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"])
            for theme in themes:
                seed.pc_playing(server.ids[2], server.ids[:6], position=61, playing=True)
                ctx, page, errors = open_page(browser, server, theme, lang=lang)
                snap = lambda name: page.screenshot(path=str(out / f"{theme}-{name}.png"))  # noqa: E731
                snap("1-home")
                page.evaluate("document.querySelector('.view.root').scrollTo(0, 520)")
                page.wait_for_timeout(300)
                snap("2-home-scrolled")
                page.evaluate("document.querySelector('.view.root').scrollTo(0, 0)")
                page.click('.mm-seg [data-sub="library"]')
                page.wait_for_selector(".mm-row", timeout=5000)
                page.wait_for_timeout(500)
                snap("3-library")
                page.click('.mm-seg [data-sub="playlists"]')
                page.wait_for_selector(".mm-tile[data-pl]", timeout=5000)
                page.wait_for_timeout(500)
                snap("4-playlists")
                page.click(f'.mm-tile[data-pl="{server.playlist}"]')
                page.wait_for_selector(".mm-ph", timeout=5000)
                page.wait_for_timeout(700)
                snap("5-playlist")
                page.click(".mm-ph-play")                      # plays on the phone → mini player
                page.wait_for_timeout(600)
                page.evaluate("window.MemuTgApp.kit.goBack()")
                page.wait_for_timeout(500)
                snap("6-mini")
                page.evaluate("window.MemuMusicTg.openFull()")
                page.wait_for_timeout(1200)
                snap("7-player")
                page.evaluate("window.MemuMusicTg.playQueue([...window.MemuMusicTg.state.queue], 0)")
                page.evaluate("(() => { const s = window.MemuMusicTg.state; s.index = 0; })()")
                page.click(".mm-tab-lyrics")
                page.wait_for_timeout(1500)
                snap("8-lyrics")
                page.click(".mm-tab-lyrics")
                page.click(".mm-tab-queue")
                page.wait_for_timeout(600)
                snap("9-queue")
                page.evaluate("window.MemuMusicTg.closeFull()")
                page.wait_for_timeout(500)
                page.evaluate("window.MemuMusicTg.setMode('pc')")
                page.wait_for_timeout(800)
                page.evaluate("window.MemuMusicTg.openFull()")
                page.wait_for_timeout(1200)
                snap("10-remote")
                page.evaluate("window.MemuMusicTg.closeFull(); window.MemuMusicTg.setMode('phone')")
                page.wait_for_timeout(400)
                page.click('.mm-seg [data-sub="device"]')
                page.wait_for_timeout(800)
                snap("11-device-empty")
                page.evaluate(f"""(async () => {{ const st = window.MemuMusicTg; const rows = (await (await fetch('api/music.list', {{method:'POST',
                    headers: {{'Content-Type':'application/json', Authorization: 'Bearer ' + window.MemuTgApp.state.token}}, body: '{{}}'}})).json()).tracks;
                    for (const t of rows.slice(0, 3)) await st.saveOffline(t); }})()""")
                page.click('.mm-seg [data-sub="home"]')
                page.wait_for_timeout(300)
                page.click('.mm-seg [data-sub="device"]')
                page.wait_for_timeout(900)
                snap("12-device")
                page.evaluate("window.MemuMusicTg.linkSheet('https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC')")
                page.wait_for_timeout(1200)
                snap("13-link")
                errors_all += [f"{theme}: {e}" for e in errors]
                ctx.close()
            browser.close()
    finally:
        server.stop()
    return errors_all


# text that is cut although it should not be: labels / headings (track titles may end with … on purpose)
CLIPPED_JS = """() => [...document.querySelectorAll('.mm-root *, .mm-stack *, .mm-full *, .mm-mini *, .sheet *')]
  .filter((el) => el.children.length === 0 && el.textContent.trim() && el.offsetParent !== null
    && el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflow !== 'visible')
  .map((el) => (el.className || el.tagName) + ': ' + el.textContent.trim().slice(0, 40))"""


def shots_extra(out: Path, themes, lang="ru"):
    """Wave 2: 360×740 and 390×844 phones (clipped-text check), artists / album screens, the «bring back» banner,
    the offline start (no computer), Telegram Desktop (wide split layout)."""
    from playwright.sync_api import sync_playwright
    import round37_music_tg_seed as seed
    out.mkdir(parents=True, exist_ok=True)
    server = Server(lang).start()
    seed.seed_artist()
    report = {"errors": [], "clipped": {}}
    try:
        with sync_playwright() as play:
            browser = play.chromium.launch(executable_path=chromium(), args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"])
            for theme in themes:
                for size in ((360, 740), (390, 844)):
                    tag = f"{theme}-{size[0]}"
                    seed.pc_playing(server.ids[2], server.ids[:6], position=61, playing=True)
                    ctx, page, errors = open_page(browser, server, theme, size=size, lang=lang)
                    snap = lambda name: page.screenshot(path=str(out / f"{tag}-{name}.png"))  # noqa: E731
                    clipped = lambda name: report["clipped"].setdefault(f"{tag}-{name}", page.evaluate(CLIPPED_JS))  # noqa: E731
                    snap("1-home"); clipped("home")
                    page.click('.mm-seg [data-sub="library"]')
                    page.wait_for_selector(".mm-row", timeout=5000)
                    page.wait_for_timeout(400)
                    snap("2-library"); clipped("library")
                    page.click('.mm-views [data-v="artists"]')
                    page.wait_for_selector(".mm-artist", timeout=5000)
                    page.wait_for_timeout(500)
                    snap("3-artists"); clipped("artists")
                    page.click('.mm-artist:has-text("Neon Harbor")')
                    page.wait_for_selector(".mm-ah", timeout=5000)
                    page.wait_for_timeout(800)
                    snap("4-artist"); clipped("artist")
                    page.evaluate("document.querySelector('.view.mm-stack:last-of-type, .view.mm-stack').scrollTo(0, 420)")
                    page.wait_for_timeout(300)
                    snap("5-artist-scrolled")
                    page.click('.mm-card[data-al]')
                    page.wait_for_selector(".mm-album", timeout=5000)
                    page.wait_for_timeout(800)
                    snap("6-album"); clipped("album")
                    page.evaluate("window.MemuTgApp.kit.goBack(); window.MemuTgApp.kit.goBack()")
                    page.wait_for_timeout(500)
                    page.click('.mm-views [data-v="albums"]')
                    page.wait_for_selector(".mm-tile[data-al]", timeout=5000)
                    page.wait_for_timeout(500)
                    snap("7-albums"); clipped("albums")
                    page.click('.mm-views [data-v="tracks"]')
                    page.wait_for_timeout(300)
                    page.click('.mm-seg [data-sub="home"]')
                    page.wait_for_timeout(300)
                    page.click('.mm-continue')
                    page.wait_for_timeout(700)
                    page.evaluate("window.MemuMusicTg.openFull()")
                    page.wait_for_timeout(1100)
                    snap("8-player"); clipped("player")
                    page.evaluate("window.MemuMusicTg.closeFull()")
                    page.wait_for_timeout(400)
                    # «bring back»: the phone remembers two saved tracks the store of this address does not have
                    page.evaluate(f"(() => {{ const st = window.MemuMusicTg.state; st.stable = {{ {json.dumps(server.ids[0])}: {{}}, {json.dumps(server.ids[1])}: {{}} }}; }})()")
                    page.click('.mm-seg [data-sub="device"]')
                    page.wait_for_selector(".mm-restore", timeout=5000)
                    page.wait_for_timeout(500)
                    snap("9-restore"); clipped("restore")
                    report["errors"] += [f"{tag}: {e}" for e in errors]
                    ctx.close()
            # Telegram Desktop: a wide window → split layout
            seed.pc_playing(server.ids[2], server.ids[:6], position=61, playing=True)
            ctx, page, errors = open_page(browser, server, "dark", size=(1180, 760), lang=lang)
            snap = lambda name: page.screenshot(path=str(out / f"desktop-{name}.png"))  # noqa: E731
            snap("1-home")
            page.click('.mm-seg [data-sub="library"]')
            page.wait_for_selector(".mm-row", timeout=5000)
            page.click('.mm-views [data-v="artists"]')
            page.wait_for_selector(".mm-artist", timeout=5000)
            page.click('.mm-artist:has-text("Neon Harbor")')
            page.wait_for_selector(".mm-ah", timeout=5000)
            page.wait_for_timeout(800)
            snap("2-artist")
            page.evaluate("window.MemuTgApp.kit.goBack()")
            page.click('.mm-views [data-v="tracks"]')
            page.wait_for_selector(".mm-row", timeout=5000)
            page.click(".mm-row >> nth=1")
            page.wait_for_timeout(700)
            snap("3-mini")
            page.evaluate("window.MemuMusicTg.openFull()")
            page.wait_for_timeout(1100)
            snap("4-player")
            report["errors"] += [f"desktop: {e}" for e in errors]
            ctx.close()
            browser.close()
    finally:
        server.stop()
    return report


if __name__ == "__main__":
    args = sys.argv[1:]
    target = Path(args[0] if args else "/tmp/r37-music-tg-shots")
    themes = ["dark", "light"]
    lang = "ru"
    if "--themes" in args:
        themes = args[args.index("--themes") + 1].split(",")
    if "--lang" in args:
        lang = args[args.index("--lang") + 1]
    if "--extra" in args:
        print(json.dumps(shots_extra(target, themes, lang), ensure_ascii=False, indent=1))
    else:
        problems = shots(target, themes, lang)
        print("errors:", problems)
