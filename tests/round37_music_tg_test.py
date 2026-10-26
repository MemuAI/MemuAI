"""Round 37: MemuMusic in the Telegram Mini App (app/music/tgapp_ops.py, app/tgapp/music.js/.css).

Offline: a seeded library (round37_music_tg_seed: generated covers and WAV files), signed initData, the window's
player played by this process (fake_window). The browser part (headless Chromium, skipped when it is missing)
opens the real Mini App server on a phone viewport and walks the tab, the player and the offline storage.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
TGAPP = ROOT / "app" / "tgapp"

from app import tg_webapp  # noqa: E402
from app.music import tgapp_ops  # noqa: E402
import round37_music_tg_seed as seed  # noqa: E402

TOKEN = "123456:TEST-token_for-mini-app"
USER = 4242

def init_data() -> str:
    return tg_webapp.sign_init_data({"auth_date": int(time.time()), "query_id": "AAH",
                                     "user": {"id": USER, "first_name": "Yono"}}, TOKEN)

class Static(unittest.TestCase):
    def read(self, name):
        return (TGAPP / name).read_text(encoding="utf-8")

    def test_files_are_wired(self):
        html = self.read("index.html")
        self.assertIn('music.css?v=1.4-r41', html)
        self.assertIn('music.js?v=1.3-r41', html)
        self.assertLess(html.index("music.js"), html.index("app.js?v="))
        app = self.read("app.js")
        self.assertIn("addTab(tab, render)", app)
        self.assertIn("kit.tabViews[tab]", app)
        self.assertIn("pushBack, popBack", app)

    def test_every_phrase_is_bilingual(self):
        js = self.read("music.js")
        for match in re.finditer(r'tr\("((?:[^"\\]|\\.)*)",\s*(`|")', js):
            self.assertRegex(match.group(1), r"[А-Яа-яЁё…·%$]|^\s*$|^[\w\s]+$", match.group(0))
        self.assertNotRegex(js, r'tr\("[^"]*",\s*""\)')
        self.assertGreater(js.count("tr("), 150)

    def test_light_on_the_phone(self):
        js = self.read("music.js")
        css = self.read("music.css")
        self.assertNotRegex(js, r"setInterval\([^,]+,\s*(?:[1-9]\d{0,2})\)")
        self.assertIn("document.hidden", js)
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn("animation-play-state: paused", css)
        self.assertIn("html.mm-light", css)
        self.assertNotIn("backdrop-filter", css)
        self.assertIn("navigator.mediaSession", js)
        self.assertIn("HapticFeedback", (TGAPP / "app.js").read_text(encoding="utf-8"))
        self.assertIn('haptic("', js)
        self.assertIn("caches.open", js)
        self.assertIn("navigator.storage.estimate", js)

class Unit(unittest.TestCase):
    def test_urls_rewrite_to_the_mini_app(self):
        urls = tgapp_ops.Urls("TOK")
        row = urls.track({"id": "abc123def456", "title": "T", "artist": "A", "duration": 3,
                          "cover": "/api/pg/music/cover/abc123def456?t=x&v=1f2", "stream": "/api/pg/music/stream/abc123def456?t=x",
                          "file": "C:/secret/path.m4a"})
        self.assertEqual(row["cover"], "music/cover/abc123def456?t=TOK&v=1f2")
        self.assertEqual(row["stream"], "music/stream/abc123def456?t=TOK")
        self.assertNotIn("file", row)
        self.assertIsNone(urls.track({"id": "x", "title": "t", "artist": "", "cover": None})["cover"])
        pl = urls.playlist({"id": "smart:top", "cover": "http://127.0.0.1:8600/api/pg/music/cover/aaa?t=1",
                            "covers": ["/api/pg/music/cover/bbb?t=2"]})
        self.assertEqual(pl["cover"], "music/cover/aaa?t=TOK")
        self.assertEqual(pl["covers"], ["music/cover/bbb?t=TOK"])

    def test_media_token_scope(self):
        tg_webapp.save_config({"enabled": True, "mode": "cloudflare"})
        from app.pgdata import save_preferences
        save_preferences({"telegram_bot_token": TOKEN, "telegram_chat_id": str(USER)})
        secret = tg_webapp.load_config()["secret"]
        token, _ = tgapp_ops.issue_media_token(str(USER), secret)
        self.assertTrue(tgapp_ops.media_token_ok(token))
        app_token, _ = tg_webapp.issue_token(str(USER), secret)
        self.assertFalse(tgapp_ops.media_token_ok(app_token))
        self.assertFalse(tgapp_ops.media_token_ok(""))
        stranger, _ = tgapp_ops.issue_media_token("999", secret)
        self.assertFalse(tgapp_ops.media_token_ok(stranger))

    def test_player_view(self):
        urls = tgapp_ops.Urls("T")
        self.assertFalse(tgapp_ops._player_view(None, urls)["online"])
        old = tgapp_ops._player_view({"track": {"id": "a", "title": "x", "artist": ""}, "playing": True,
                                      "updated_at": time.time() - 3600}, urls)
        self.assertFalse(old["online"])
        fresh = tgapp_ops._player_view({"track": {"id": "a", "title": "x", "artist": ""}, "playing": True,
                                        "updated_at": time.time(), "position_now": 12.5, "volume": .4}, urls)
        self.assertTrue(fresh["online"])
        self.assertEqual(fresh["position"], 12.5)
        self.assertEqual(fresh["track"]["stream"], "music/stream/a?t=T")

    def test_window_url_from_env(self):
        os.environ["MEMUAI_MUSIC_WINDOW_URL"] = ""
        try:
            self.assertEqual(tgapp_ops.window_url(), "")
            with self.assertRaises(tgapp_ops.PcOffline):
                tgapp_ops.call_sync("player.state", {})
        finally:
            del os.environ["MEMUAI_MUSIC_WINDOW_URL"]

class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from app.pgdata import save_preferences
        save_preferences({"telegram_bot_token": TOKEN, "telegram_chat_id": str(USER), "memes": [], "chat_memes": {}})
        tg_webapp.save_config({"enabled": True, "mode": "cloudflare"})
        cls.lib, cls.ids, cls.playlist = seed.seed()
        from app.tgapp_api import create_tgapp, auth_failures, auth_limiter
        auth_failures._hits.clear()
        auth_limiter._hits.clear()
        os.environ["MEMUAI_MUSIC_WINDOW_URL"] = ""
        cls.client = TestClient(create_tgapp())
        cls.client.__enter__()
        cls.token = cls.client.post("/tgapp/api/auth", json={"init_data": init_data()}).json()["token"]

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        os.environ.pop("MEMUAI_MUSIC_WINDOW_URL", None)
        tg_webapp.save_config({"enabled": False})

    def post(self, name, body=None, status=200):
        response = self.client.post("/tgapp/api/" + name, json=body or {}, headers={"Authorization": "Bearer " + self.token})
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def test_needs_a_session(self):
        self.assertEqual(self.client.post("/tgapp/api/music.home", json={}).status_code, 401)

    def test_home_lists_and_rewrites(self):
        data = self.post("music.home")
        self.assertEqual(data["stats"]["tracks"], len(self.ids))
        self.assertTrue(data["liked"])
        self.assertTrue(all(t["stream"].startswith("music/stream/") for t in data["added"]))
        self.assertTrue(all((t["cover"] or "").startswith("music/cover/") for t in data["added"]))
        self.assertFalse(data["player"]["online"])
        self.assertTrue(any(p["id"] == self.playlist for p in data["playlists"]))
        self.assertNotIn("/api/pg/", json.dumps(data))

    def test_stream_with_range_and_cover(self):
        track = self.post("music.list", {"q": "Midnight"})["tracks"][0]
        whole = self.client.get("/tgapp/" + track["stream"])
        self.assertEqual(whole.status_code, 200)
        self.assertEqual(whole.headers["accept-ranges"], "bytes")
        part = self.client.get("/tgapp/" + track["stream"], headers={"Range": "bytes=0-99"})
        self.assertEqual(part.status_code, 206)
        self.assertEqual(len(part.content), 100)
        self.assertTrue(part.headers["content-range"].startswith("bytes 0-99/"))
        self.assertEqual(part.content[:4], b"RIFF")
        cover = self.client.get("/tgapp/" + track["cover"])
        self.assertEqual(cover.status_code, 200)
        self.assertEqual(cover.content[:2], b"\xff\xd8")
        bad = track["stream"].split("?")[0] + "?t=" + self.token
        self.assertEqual(self.client.get("/tgapp/" + bad).status_code, 403)
        self.assertEqual(self.client.get("/tgapp/music/stream/../../x?t=1").status_code, 404)

    def test_like_sync_and_playlists(self):
        tid = self.ids[-1]
        self.post("music.like", {"id": tid, "liked": True})
        self.assertTrue(self.lib.get(tid)["liked"])
        plays_before = self.lib.get(tid)["plays"]
        out = self.post("music.sync", {"likes": [{"id": tid, "liked": False}], "plays": [{"id": tid, "seconds": 200}],
                                       "playlist_adds": [{"playlist": self.playlist, "ids": [tid]}]})
        self.assertEqual(out["applied"], {"likes": 1, "plays": 1, "playlists": 1})
        self.assertFalse(self.lib.get(tid)["liked"])
        self.assertEqual(self.lib.get(tid)["plays"], plays_before + 1)
        pl = self.post("music.playlist", {"id": self.playlist})["playlist"]
        self.assertIn(tid, [t["id"] for t in pl["tracks"]])
        created = self.post("music.playlist", {"action": "create", "name": "Дорога", "track_ids": self.ids[:2]})["playlist"]
        self.post("music.playlist", {"action": "update", "id": created["id"], "name": "Дорога домой", "remove": [self.ids[0]]})
        got = self.post("music.playlist", {"id": created["id"]})["playlist"]
        self.assertEqual(got["name"], "Дорога домой")
        self.assertEqual([t["id"] for t in got["tracks"]], [self.ids[1]])
        self.post("music.playlist", {"action": "delete", "id": created["id"]})
        self.post("music.playlist", {"action": "create", "name": "  "}, status=400)

    def test_lyrics_and_detect(self):
        lyrics = self.post("music.lyrics", {"id": self.ids[0]})
        self.assertEqual(lyrics["synced"][0][1], seed.LYRICS[0][1])
        self.post("music.detect", {"url": "file:///etc/passwd"}, status=400)
        self.assertEqual(self.post("music.detect", {"url": "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC"})["source"], "spotify")
        self.post("music.download", {"url": "javascript:alert(1)"}, status=400)

    def test_remote_without_the_window(self):
        self.assertEqual(self.post("music.remote"), {"online": False, "available": False})
        self.assertEqual(self.post("music.remote", {"cmd": "pause"}, status=409)["code"], "pc_offline")
        self.post("music.remote", {"cmd": "rm -rf"}, status=400)

    def test_remote_and_handoff_with_the_window(self):
        from app.music.player import hub
        patches = seed.fake_window()
        for patch in patches:
            patch.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        seed.pc_playing(self.ids[2], self.ids[:5], position=61)
        state = self.post("music.remote")
        self.assertTrue(state["online"])
        self.assertTrue(state["playing"])
        self.assertEqual(state["track"]["id"], self.ids[2])
        self.assertEqual([t["id"] for t in state["queue"]], self.ids[:5])
        self.assertEqual(state["index"], 2)
        player = hub(self.lib.root)
        seq = player.cmd_seq
        self.post("music.remote", {"cmd": "seek", "position": 90})
        self.post("music.remote", {"cmd": "volume", "value": 0.3})
        cmds = player.pending(seq)
        self.assertEqual([(c["cmd"], c["args"].get("position"), c["args"].get("value")) for c in cmds],
                         [("seek", 90.0, None), ("volume", None, 0.3)])
        self.post("music.remote", {"cmd": "play_playlist", "playlist_id": self.playlist})
        last = player.pending(seq)[-1]
        self.assertEqual(last["cmd"], "play_track")
        self.assertTrue(last["args"]["queue"])

        seq = player.cmd_seq
        taken = self.post("music.handoff", {"to": "phone"})
        self.assertEqual(taken["track"]["id"], self.ids[2])
        self.assertGreaterEqual(taken["position"], 61)
        self.assertEqual([c["cmd"] for c in player.pending(seq)], ["pause"])

        seq = player.cmd_seq
        self.post("music.handoff", {"to": "pc", "track_id": self.ids[3], "position": 33, "queue": self.ids[:4]})
        cmd = player.pending(seq)[-1]
        self.assertEqual((cmd["cmd"], cmd["args"]["track_id"], cmd["args"]["position"]), ("play_track", self.ids[3], 33.0))
        player.update({"track_id": "", "playing": False})
        self.assertEqual(self.post("music.handoff", {"to": "phone"}, status=409)["code"], "nothing_playing")

def _chromium() -> str:
    for path in (os.environ.get("MEMU_TEST_CHROMIUM", ""), "/workspace/work/chromium/chrome", "/usr/bin/chromium"):
        if path and Path(path).exists():
            return path
    return ""

@unittest.skipUnless(_chromium(), "headless Chromium is not available")
class Browser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        from round37_music_tg_shots import Server
        os.environ.pop("MEMUAI_MUSIC_WINDOW_URL", None)
        cls.server = Server("ru").start()
        cls.play = sync_playwright().start()
        cls.browser = cls.play.chromium.launch(executable_path=_chromium(),
                                               args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.play.stop()
        cls.server.stop()
        tg_webapp.save_config({"enabled": False})

    def test_phone_walkthrough(self):
        from round37_music_tg_shots import open_page
        seed.pc_playing(self.server.ids[2], self.server.ids[:5], position=20)
        ctx, page, errors = open_page(self.browser, self.server, "dark")
        self.addCleanup(ctx.close)
        self.assertTrue(page.query_selector('.tab[data-tab="music"].active'))
        self.assertTrue(page.query_selector("#mm-pc-card.mm-pc"), "what plays on the computer")
        self.assertGreaterEqual(len(page.query_selector_all(".mm-card")), 4)

        self.assertLessEqual(page.evaluate("document.querySelector('.view.root').scrollWidth"), 390)
        page.click('.mm-seg [data-sub="library"]')
        page.wait_for_selector(".mm-row")
        page.fill("#mm-q", "Ocean")
        page.wait_for_timeout(700)
        self.assertEqual(len(page.query_selector_all("#mm-lib .mm-row")), 1)
        page.click("#mm-lib .mm-row")
        page.wait_for_timeout(600)
        self.assertFalse(page.query_selector("#mm-mini").is_hidden())
        self.assertTrue(page.evaluate("!window.MemuMusicTg.state.audio.paused || window.MemuMusicTg.state.audio.readyState >= 1"))
        page.evaluate("window.MemuMusicTg.openFull()")
        page.wait_for_selector(".mm-full.open")
        self.assertEqual(page.inner_text(".mm-meta h2"), "Ocean Eyes")
        page.evaluate("window.MemuMusicTg.closeFull()")
        page.wait_for_timeout(500)

        page.evaluate("""(async () => { const st = window.MemuMusicTg; await st.saveOffline(st.state.queue[0]); })()""")
        page.click('.mm-seg [data-sub="device"]')
        page.wait_for_selector("#mm-off-list .mm-row")
        self.assertIn("КБ", page.inner_text(".mm-store-top b"))
        played = page.evaluate("""(async () => { const st = window.MemuMusicTg; const id = st.state.queue[0].id;
            const info = await st.storageInfo(); await st.dropOffline(id); return [info.count, (await st.storageInfo()).count]; })()""")
        self.assertEqual(played, [1, 0])

        page.evaluate("window.MemuMusicTg.setMode('pc')")
        page.wait_for_timeout(900)
        self.assertIn("Компьютер", page.inner_text("#mm-mini"))
        page.evaluate("window.MemuMusicTg.setMode('phone')")
        self.assertEqual(errors, [])

    def test_english(self):
        from app.pgdata import load_preferences, save_preferences
        from round37_music_tg_shots import open_page
        prefs = load_preferences()
        save_preferences({**prefs, "language": "en"})
        self.addCleanup(lambda: save_preferences({**load_preferences(), "language": "ru"}))
        ctx, page, errors = open_page(self.browser, self.server, "light", lang="en")
        self.addCleanup(ctx.close)
        text = page.inner_text("#app")
        self.assertIn("Library", text)
        shown = page.inner_text(".mm-root")
        for title, artist, album, *_ in seed.TRACKS:
            shown = shown.replace(title, "").replace(artist, "").replace(album, "")
        shown = shown.replace("Вечер пятницы", "")
        self.assertNotRegex(shown, r"[А-Яа-яЁё]{2,}")
        self.assertEqual(errors, [])

if __name__ == "__main__":
    unittest.main()
