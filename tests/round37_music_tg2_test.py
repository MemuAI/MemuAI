"""Round 37 (wave 2): MemuMusic in the Mini App and the bot — offline that survives a new tunnel address, the phone
reporting what it plays, artists / albums, the app shell for opening without the computer, /music in the bot.

Offline (no network): the seeded library of round37_music_tg_seed, signed initData, mocked Bot API calls.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
TGAPP = ROOT / "app" / "tgapp"

from app import tg_webapp  # noqa: E402
from app.music import tgapp_ops, telegram_music  # noqa: E402
import round37_music_tg_seed as seed  # noqa: E402

TOKEN = "123456:TEST-token_for-mini-app"
USER = 4242


def init_data() -> str:
    return tg_webapp.sign_init_data({"auth_date": int(time.time()), "query_id": "AAH",
                                     "user": {"id": USER, "first_name": "Yono"}}, TOKEN)


class Static(unittest.TestCase):
    def read(self, name):
        return (TGAPP / name).read_text(encoding="utf-8")

    def test_offline_keys_do_not_depend_on_the_address(self):
        js = self.read("music.js")
        self.assertIn('const OFF_BASE = "https://memu-music.offline/"', js)
        self.assertRegex(js, r"offKey = \(id, pc = pcOf\(id\)\) => `\$\{OFF_BASE\}\$\{pc\}/track/")
        self.assertIn("DeviceStorage", js)                      # the list of saved tracks outlives the origin
        self.assertIn("CloudStorage", js)
        self.assertIn('api("music.tracks"', js)                 # «Вернуть N треков»
        self.assertIn("legacyKeys", js)                         # first-wave keys are moved, not lost

    def test_service_worker_shell(self):
        sw = self.read("sw.js")
        self.assertIn("networkFirst", sw)
        self.assertRegex(sw, r"SKIP = /\^\(api\\/\|m\\/\|music\\/")   # API, module documents, streams: never cached
        self.assertIn("status >= 500", sw)                       # the tunnel's error page → the saved shell
        self.assertNotIn("telegram.org", sw.split("*/", 1)[1])   # the worker's CSP is connect-src 'self'
        js = self.read("music.js")
        self.assertIn('navigator.serviceWorker.register("sw.js", { scope: "./" })', js)
        app = self.read("app.js")
        self.assertIn("offlineBoot: []", app)
        self.assertIn('id="boot-away"', app)

    def test_phone_reports_lightly(self):
        js = self.read("music.js")
        self.assertIn('api("music.report"', js)
        self.assertIn("now - lastReport < 30000", js)            # every 30 s while playing, else only on events
        self.assertIn("keepalive: true", js)                     # closing the app says «not playing any more»

    def test_artist_and_album_screens(self):
        js = self.read("music.js")
        for needle in ("function openArtist(", "function openAlbum(", 'kind: "artist"', 'kind: "album"', 'data-v="${id}"'):
            self.assertIn(needle, js)
        css = self.read("music.css")
        self.assertIn(".mm-ah-art", css)
        self.assertIn('.app[data-tab="music"]:not(.has-detail)', css)   # Telegram Desktop: one wide page

    def test_bilingual_new_strings(self):
        js = self.read("music.js")
        for match in re.finditer(r'tr\("((?:[^"\\]|\\.)*)",\s*(`|")', js):
            self.assertRegex(match.group(1), r"[А-Яа-яЁё…·%$]|^\s*$|^[\w\s]+$", match.group(0))


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from app.pgdata import save_preferences
        save_preferences({"telegram_bot_token": TOKEN, "telegram_chat_id": str(USER), "memes": [], "chat_memes": {}})
        tg_webapp.save_config({"enabled": True, "mode": "cloudflare"})
        cls.lib, cls.ids, cls.playlist = seed.seed()
        seed.seed_artist()
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

    def test_pc_id_is_stable(self):
        first = self.post("music.home")["pc_id"]
        self.assertRegex(first, r"^[0-9a-f]{12}$")
        self.assertEqual(self.post("music.home")["pc_id"], first)
        self.assertEqual(tgapp_ops.pc_id(), first)

    def test_tracks_by_ids_for_bringing_back(self):
        data = self.post("music.tracks", {"ids": [self.ids[1], "nope", "../x", self.ids[0]]})
        self.assertEqual([t["id"] for t in data["tracks"]], [self.ids[1], self.ids[0]])
        self.assertTrue(all(t["stream"].startswith("music/stream/") for t in data["tracks"]))
        self.assertEqual(data["pc_id"], tgapp_ops.pc_id())

    def test_artist_and_album(self):
        artist = self.post("music.browse", {"kind": "artist", "name": "Neon Harbor"})
        self.assertGreaterEqual(len(artist["tracks"]), 4)
        self.assertTrue(artist["cover"].startswith("music/cover/"))
        self.assertTrue(artist["albums"] and all((a["cover"] or "").startswith("music/cover/") for a in artist["albums"]))
        key = artist["albums"][0]["key"]
        album = self.post("music.browse", {"kind": "album", "key": key})
        self.assertEqual([t["track_no"] for t in album["items"]][:4], [1, 2, 3, 4])
        self.assertTrue(all(t["stream"].startswith("music/stream/") for t in album["items"]))
        self.assertNotIn("/api/pg/", str(album))
        artists = self.post("music.browse", {"kind": "artists"})["items"]
        self.assertIn("Neon Harbor", [a["name"] for a in artists])

    def test_report_without_the_window(self):
        self.assertEqual(self.post("music.report", {"track_id": self.ids[0], "playing": True}),
                         {"ok": False, "reason": "pc_offline"})
        self.post("music.report", {}, status=400)

    def test_report_with_the_window(self):
        from app.music.player import hub
        patches = seed.fake_window()
        for patch in patches:
            patch.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        player = hub(self.lib.root)
        seed.pc_playing(self.ids[2], self.ids[:5], position=10, playing=True)
        # the computer plays: the phone does not take over its state
        self.assertEqual(self.post("music.report", {"track_id": self.ids[0], "playing": True})["reason"], "pc_playing")
        self.assertEqual(player.snapshot()["device"], "pc")
        player.update({"playing": False})
        out = self.post("music.report", {"track_id": self.ids[0], "playing": True, "position": 12.5, "duration": 16,
                                         "queue": self.ids[:3], "index": 0})
        self.assertTrue(out["ok"])
        state = player.snapshot()
        self.assertEqual((state["track_id"], state["device"], state["playing"], state["queue"]), (self.ids[0], "phone", True, self.ids[:3]))
        self.assertEqual(state["device_name"], "Телефон")
        # the phone does not show its own playback as «Играет на ПК»
        remote = self.post("music.remote")
        self.assertEqual((remote["online"], remote["device"]), (False, "phone"))
        self.post("music.report", {"track_id": self.ids[0], "playing": False, "position": 14})
        self.assertFalse(player.snapshot()["playing"])
        player.update({"device": "pc", "track_id": "", "playing": False})

    def test_service_worker_is_served(self):
        response = self.client.get("/tgapp/sw.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("javascript", response.headers["content-type"])


class Bot(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(telegram_music.classify("https://open.spotify.com/album/1A2B3c?si=x"), "music")
        self.assertEqual(telegram_music.classify("  https://music.yandex.ru/album/1/track/2  "), "music")
        self.assertEqual(telegram_music.classify("https://music.youtube.com/watch?v=abc"), "music")
        self.assertEqual(telegram_music.classify("https://soundcloud.com/a/b"), "music")
        self.assertEqual(telegram_music.classify("https://youtu.be/dQw4w9WgXcQ"), "youtube")
        self.assertEqual(telegram_music.classify("https://www.youtube.com/watch?v=dQw4w9WgXcQ"), "youtube")
        self.assertEqual(telegram_music.classify("что это за песня https://open.spotify.com/track/1"), "")
        self.assertEqual(telegram_music.classify("https://example.com/song.mp3"), "")
        self.assertEqual(telegram_music.classify("https://evil.com/open.spotify.com/x"), "")
        self.assertEqual(telegram_music.classify("https://open.spotify.com.evil.com/x"), "")

    def test_progress_text(self):
        running = {"status": "running", "progress": .5, "source": "spotify", "item_kind": "album", "title": "Afterglow",
                   "total": 10, "done": 5, "job_id": "j1"}
        text = telegram_music.progress_text(running, "ru")
        self.assertIn("Spotify · альбом", text)
        self.assertIn("50% · 5/10", text)
        self.assertIn("▰▰▰▰▰▰▱▱▱▱▱▱", text)
        done = telegram_music.progress_text(dict(running, status="done", track_ids=["a", "b", "c"]), "en")
        self.assertIn("Downloaded to MemuMusic — 3 tracks", done)
        err = telegram_music.progress_text(dict(running, status="error", error="not a bot"), "ru")
        self.assertIn("Не удалось", err)
        self.assertIn("not a bot", err)
        self.assertEqual(telegram_music.progress_text({"status": "queued", "source": "yandex"}, "ru").count("Яндекс Музыка"), 1)

    def test_command_follows_the_job_to_the_end(self):
        from app import telegram_bot as tb
        jobs = [[{"job_id": "j1", "status": "running", "progress": .3, "source": "spotify", "item_kind": "track", "title": "Song"}],
                [{"job_id": "j1", "status": "done", "progress": 1, "source": "spotify", "item_kind": "track", "title": "Song",
                  "track_ids": ["t1"]}]]
        calls = []

        async def fake_call(op, args=None, lang="ru"):
            calls.append(op)
            if op == "download.start":
                return {"job_id": "j1", "source": "spotify", "kind": "track"}
            return jobs.pop(0) if len(jobs) > 1 else jobs[0]

        sent, edits = [], []

        async def fake_send(client, token, chat_id, text, markup=None, **kw):
            sent.append((text, markup))
            return [77]

        async def fake_tb_call(client, token, method, **payload):
            edits.append(payload)
            return {"ok": True}

        with mock.patch.object(tgapp_ops, "call", fake_call), mock.patch.object(tb, "_send", fake_send), \
                mock.patch.object(tb, "_call", fake_tb_call), mock.patch.object(tb, "_webapp_url", lambda: "https://x.trycloudflare.com/tgapp/"), \
                mock.patch.object(telegram_music, "POLL_SECONDS", 0.01), mock.patch.object(telegram_music, "EDIT_EVERY", 0):
            asyncio.run(telegram_music.start(None, TOKEN, "42", {"language": "ru"}, "https://open.spotify.com/track/1"))
        self.assertEqual(calls[0], "download.start")
        self.assertIn("mus:x:j1", str(sent[0][1]))                   # a cancel button while it runs
        self.assertTrue(edits and edits[-1]["message_id"] == 77)
        self.assertIn("Скачано в MemuMusic", edits[-1]["text"])
        self.assertEqual(edits[-1]["reply_markup"]["inline_keyboard"][0][0]["web_app"]["url"],
                         "https://x.trycloudflare.com/tgapp/?tab=music")

    def test_bare_youtube_link_asks(self):
        from app import telegram_bot as tb
        sent, started = [], []

        async def fake_send(client, token, chat_id, text, markup=None, **kw):
            sent.append(markup)
            return [5]

        async def fake_start(*a, **kw):
            started.append(a[4])

        async def fake_delete(*a):
            return True

        def spawn(coro):
            asyncio.get_event_loop().create_task(coro)

        async def run():
            with mock.patch.object(tb, "_send", fake_send), mock.patch.object(telegram_music, "start", fake_start), \
                    mock.patch.object(tb, "_delete_quietly", fake_delete), mock.patch.object(tb, "_spawn_answer", spawn):
                self.assertTrue(await telegram_music.handle_text(None, TOKEN, "42", {}, "https://youtu.be/dQw4w9WgXcQ"))
                self.assertIn("mus:dl", str(sent[0]))
                note = await telegram_music.handle_callback(None, TOKEN, "42", 5, "mus:dl", {})
                await asyncio.sleep(0)
                self.assertTrue(note)
                self.assertFalse(await telegram_music.handle_text(None, TOKEN, "42", {}, "привет"))
                self.assertFalse(await telegram_music.handle_text(None, TOKEN, "42", {"telegram_music_links": False},
                                                                  "https://open.spotify.com/track/1"))
        asyncio.run(run())
        self.assertEqual(started, ["https://youtu.be/dQw4w9WgXcQ"])

    def test_wired_into_the_bot(self):
        from app import telegram_bot as tb
        self.assertIn("music", [c["command"] for c in tb._commands("ru")])
        source = (ROOT / "app" / "telegram_bot.py").read_text(encoding="utf-8")
        self.assertIn('elif command in ("/music", "/song", "/музыка"):', source)
        self.assertIn('data.startswith("mus:")', source)
        self.assertIn("telegram_music.handle_text", source)
        self.assertIn("/music", tb._help_text({"language": "en"}))
        self.assertIn("/music", tb._help_text({"language": "ru"}))


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
        seed.seed_artist()
        cls.play = sync_playwright().start()
        cls.browser = cls.play.chromium.launch(executable_path=_chromium(),
                                               args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.play.stop()
        cls.server.stop()
        tg_webapp.save_config({"enabled": False})

    def test_artist_album_and_no_clipped_labels(self):
        from round37_music_tg_shots import open_page, CLIPPED_JS
        seed.pc_playing(self.server.ids[2], self.server.ids[:5], position=20)
        ctx, page, errors = open_page(self.browser, self.server, "dark", size=(360, 740))
        self.addCleanup(ctx.close)
        self.assertEqual(page.evaluate(CLIPPED_JS), [])
        self.assertIn("ПК", page.inner_text("#mm-pc-card small"))
        page.click('.mm-seg [data-sub="library"]')
        page.click('.mm-views [data-v="artists"]')
        page.click('.mm-artist:has-text("Neon Harbor")')
        page.wait_for_selector(".mm-ah h2")
        self.assertEqual(page.inner_text(".mm-ah h2"), "Neon Harbor")
        self.assertGreaterEqual(len(page.query_selector_all("#mm-ar-all .mm-row")), 4)
        page.click(".mm-card[data-al]")
        page.wait_for_selector(".mm-album .mm-num, .mm-num")
        self.assertEqual(page.inner_text(".mm-row.numbered .mm-num >> nth=0"), "1")
        self.assertEqual(page.evaluate(CLIPPED_JS), [])
        page.click("#mm-al-list .mm-row >> nth=0")
        page.wait_for_timeout(500)
        self.assertEqual(page.evaluate("window.MemuMusicTg.state.queue[0].title"), "Neon Lights")
        self.assertEqual(errors, [])

    def test_offline_start_and_bring_back(self):
        from round37_music_tg_shots import open_page
        ctx, page, errors = open_page(self.browser, self.server, "light")
        self.addCleanup(ctx.close)
        pc = page.evaluate("window.MemuMusicTg.state.pcId")
        self.assertRegex(pc, r"^[0-9a-f]{12}$")
        page.evaluate("""(async () => { const st = window.MemuMusicTg; const rows = (await (await fetch('api/music.list', {method:'POST',
            headers: {'Content-Type':'application/json', Authorization: 'Bearer ' + window.MemuTgApp.state.token}, body: '{}'})).json()).tracks;
            await st.saveOffline(rows[0]); })()""")
        keys = page.evaluate("(async () => (await (await caches.open('memu-music-v1')).keys()).map((r) => r.url))()")
        self.assertTrue(any(k.startswith(f"https://memu-music.offline/{pc}/track/") for k in keys), keys)
        # the computer is gone: the start screen offers what is on the phone
        page.route("**/tgapp/api/**", lambda route: route.abort())
        page.reload()
        page.wait_for_selector("#boot-away", timeout=15000)
        self.assertIn("1", page.inner_text("#boot-away"))
        page.click("#boot-away")
        page.wait_for_selector(".mm-offline-bar")
        self.assertEqual(len(page.query_selector_all("#mm-off-list .mm-row")), 1)
        page.click("#mm-off-list .mm-row")
        page.wait_for_timeout(700)
        self.assertTrue(page.evaluate("window.MemuMusicTg.state.audio.src.startsWith('blob:')"))
        self.assertEqual([e for e in errors if "Failed to fetch" not in e], [])


if __name__ == "__main__":
    unittest.main()
