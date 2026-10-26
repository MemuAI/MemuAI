# -*- coding: utf-8 -*-
"""Round 39 · MemuMusic lyrics: several sources in parallel (fixtures, no network), ranking by title/artist/duration,
variants («Wrong lyrics» / pick), per-track offset, enhanced LRC word timing, settings, speed-aware remote position,
and the karaoke highlight following audio.currentTime at 2× (headless Chromium over the module's np.js logic)."""
import json
import os
import re
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MUSIC = ROOT / "app" / "module_builtins" / "music"

class Resp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code = status
        self._data = data
        self.text = text if text else (json.dumps(data) if data is not None else "")

    def json(self):
        return self._data if self._data is not None else json.loads(self.text)

class Client:
    """Routes by URL prefix; shared by the provider threads (list.append is atomic)."""
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, params=None, **kw):
        self.calls.append((url, params))
        for prefix, response in self.routes:
            if url.startswith(prefix):
                return response(url, params) if callable(response) else response
        return Resp(404, {"error": "nf"})

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

LRC = "\n".join(f"[00:{10 + i * 4:02d}.00]Line {i}" for i in range(8))
GENIUS_PAGE = ('<html><div data-lyrics-container="true">First &amp; line<br/>Second <a href="#"><span>line</span></a><br>'
               '<div data-exclude-from-selection="true">ad</div>Third line<br/>Fourth line</div></html>')

def routes(lrclib_hits=None, netease=True, qq=True, genius=True, ovh=True):
    out = [("https://lrclib.net/api/get", Resp(404, {})),
           ("https://lrclib.net/api/search", Resp(200, lrclib_hits or [])),
           ("https://apic-desktop.musixmatch.com/ws/1.1/token.get",
            Resp(200, {"message": {"body": {"user_token": "0" * 20}}}))]
    if netease:
        out += [("https://music.163.com/api/cloudsearch/pc", Resp(200, {"result": {"songs": [
                    {"id": 7, "name": "Numb", "ar": [{"name": "Linkin Park"}], "dt": 187000},
                    {"id": 8, "name": "Numb (Live)", "ar": [{"name": "Linkin Park"}], "dt": 250000}]}})),
                ("https://music.163.com/api/song/lyric", Resp(200, {"lrc": {"lyric":
                    "[00:00.000] 作词 : Brad Delson\n[00:01.000] 作曲 : Linkin Park\n" + LRC}}))]
    if qq:
        out += [("https://c.y.qq.com/soso/fcgi-bin/client_search_cp", Resp(200, {"data": {"song": {"list": [
                    {"songmid": "m1", "songname": "Numb", "singer": [{"name": "Linkin Park"}], "interval": 186}]}}})),
                ("https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg", Resp(200, {"lyric":
                    "[ti:Numb]\n[00:00.00]Numb - Linkin Park\n[00:01.82]Lyrics by：X\n" + LRC.replace("Line", "Q&apos;line")}))]
    if genius:
        out += [("https://genius.com/api/search/multi", Resp(200, {"response": {"sections": [{"type": "song", "hits": [
                    {"type": "song", "result": {"title": "Numb", "primary_artist": {"name": "Linkin Park"},
                                                "url": "https://genius.com/Linkin-park-numb-lyrics"}}]}]}})),
                ("https://genius.com/Linkin-park-numb-lyrics", Resp(200, text=GENIUS_PAGE))]
    if ovh:
        out += [("https://api.lyrics.ovh/v1/", Resp(200, {"lyrics": "Paroles de la chanson Numb\nA\nB\nC\nD\nE"}))]
    return out

class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MEMUAI_DATA_DIR"] = self.tmp.name
        from app.music.library import Library
        self.lib = Library(Path(self.tmp.name) / "music")
        f = Path(self.tmp.name) / "a.m4a"
        f.write_bytes(b"\0" * 64)
        self.row = self.lib.add({"file": str(f), "title": "Numb (Official Video) [Lyrics]", "artist": "Linkin Park - Topic",
                                 "duration": 187})

    def tearDown(self):
        self.lib.close()
        self.tmp.cleanup()

class ParseAndMatch(unittest.TestCase):
    def test_enhanced_lrc_and_offset_tag(self):
        from app.music import lyrics as L
        rows = L.parse_lrc("[offset:+500]\n[00:12.00]<00:12.00>Hello <00:12.60>dear <00:13.10>world\n[00:14.00]Plain line")
        self.assertEqual(rows[0][:2], [11.5, "Hello dear world"])
        self.assertEqual(rows[0][2], [[11.5, "Hello "], [12.1, "dear "], [12.6, "world"]])
        self.assertEqual(rows[1], [13.5, "Plain line"])

    def test_richsync(self):
        from app.music import lyrics as L
        body = json.dumps([{"ts": 5.0, "te": 7.0, "x": "Hi you", "l": [{"c": "Hi", "o": 0}, {"c": " ", "o": 0.4}, {"c": "you", "o": 0.5}]}])

        self.assertEqual(L._richsync(body), [[5.0, "Hi you", [[5.0, "Hi "], [5.5, "you"]], 7.0]])

    def test_cleaning_and_translit(self):
        from app.music import lyrics as L
        self.assertEqual(L.clean_title("Song (Official Video) [Lyrics] (feat. X)"), "Song")
        self.assertEqual(L.clean_title("Song ft. Somebody"), "Song")
        self.assertEqual(L.clean_artist("Linkin Park - Topic"), "Linkin Park")
        self.assertEqual(L.clean_artist("Artist feat. B, C"), "Artist")
        q = L.query_of({"title": "Кино - Группа крови (клип)", "artist": "KinoChannel", "duration": 0})
        self.assertEqual((q["artist"], q["title"]), ("Кино", "Группа крови"))
        self.assertGreater(L.similarity("Би-2", "Bi-2"), 0.85)
        self.assertGreater(L.similarity("Кино", "Kino"), 0.85)

    def test_score_prefers_duration_and_synced(self):
        from app.music import lyrics as L
        q = {"title": "Numb", "artist": "Linkin Park", "album": "", "duration": 187}
        synced = [[1, "a"], [2, "b"]]
        exact = L.score({"title": "Numb", "artist": "Linkin Park", "duration": 188, "synced": synced}, q)
        off = L.score({"title": "Numb", "artist": "Linkin Park", "duration": 196, "synced": synced}, q)
        plain = L.score({"title": "Numb", "artist": "Linkin Park", "duration": 188, "synced": None}, q)
        self.assertGreater(exact, off)
        self.assertGreater(exact, plain)
        self.assertEqual(L.score({"title": "Numb", "artist": "Linkin Park", "duration": 260}, q), 0)
        self.assertEqual(L.score({"title": "In the End", "artist": "Linkin Park", "duration": 187}, q), 0)
        self.assertEqual(L.score({"title": "Numb", "artist": "Somebody Else", "duration": 187}, q), 0)

    def test_genius_page(self):
        from app.music import lyrics as L
        self.assertEqual(L.genius_lyrics(GENIUS_PAGE), "First & line\nSecond line\nThird line\nFourth line")

class FetchChain(Base):
    def test_parallel_sources_rank_and_variants(self):
        from app.music import lyrics as L
        hits = [{"id": 1, "trackName": "Numb", "artistName": "Linkin Park", "duration": 230, "syncedLyrics": LRC, "plainLyrics": "x\ny\nz\nw"},
                {"id": 2, "trackName": "Numb", "artistName": "Linkin Park", "duration": 187.4, "syncedLyrics": None,
                 "plainLyrics": "p1\np2\np3\np4"}]
        client = Client(routes(hits))
        res = L.fetch(self.lib, self.row["id"], client_factory=lambda: client)

        self.assertIn(res["source"], ("netease", "qq"))
        self.assertTrue(res["synced"][0][1].startswith(("Line 0", "Q'line 0")))
        self.assertEqual(res["status"]["musixmatch"][:10], "Musixmatch")
        kinds = [(a["source"], a["synced"]) for a in res["alts"]]
        self.assertIn(("genius", False), kinds)
        self.assertLess(max(i for i, k in enumerate(kinds) if k[1]), min(i for i, k in enumerate(kinds) if not k[1]))
        self.assertNotIn(("lrclib", True), kinds)
        self.assertNotIn(250.0, [a["duration"] for a in res["alts"]])

        nxt = L.choose(self.lib, self.row["id"], step=1)
        self.assertEqual(nxt["choice"], 1)
        self.assertEqual(nxt["source"], res["alts"][1]["source"])
        last = L.choose(self.lib, self.row["id"], index=len(res["alts"]) - 1)
        self.assertFalse(last["alts"][last["choice"]]["synced"])
        self.assertIsNone(last["synced"])
        again = L.fetch(self.lib, self.row["id"], client_factory=lambda: Client([]))
        self.assertTrue(again["cached"])
        self.assertEqual(again["choice"], last["choice"])

    def test_plain_only_when_nothing_synced_and_setting(self):
        from app.music import lyrics as L
        res = L.fetch(self.lib, self.row["id"], client_factory=lambda: Client(routes(netease=False, qq=False)))
        self.assertIsNone(res["synced"])
        self.assertIn(res["source"], ("genius", "lyricsovh"))
        self.assertTrue(res["plain"])
        self.lib.set_settings({"lyrics_plain_fallback": False})
        res = L.fetch(self.lib, self.row["id"], client_factory=lambda: Client(routes(netease=False, qq=False)), force=True)
        self.assertIsNone(res["plain"])
        self.assertEqual(res["alts"], [])

    def test_sources_setting_order_and_off(self):
        from app.music import lyrics as L
        self.lib.set_settings({"lyrics_sources": ["qq"]})
        client = Client(routes())
        res = L.fetch(self.lib, self.row["id"], client_factory=lambda: client)
        self.assertEqual(res["source"], "qq")
        self.assertTrue(all("qq.com" in url for url, _ in client.calls))
        self.lib.set_settings({"lyrics_sources": []})
        client = Client(routes())
        res = L.fetch(self.lib, self.row["id"], client_factory=lambda: client, force=True)
        self.assertEqual((client.calls, res["synced"], res["plain"]), ([], None, None))

    def test_auto_off_and_all_down(self):
        from app.music import lyrics as L
        self.lib.set_settings({"auto_lyrics": False})
        client = Client(routes())
        res = L.fetch(self.lib, self.row["id"], client_factory=lambda: client)
        self.assertTrue(res.get("auto_off"))
        self.assertEqual(client.calls, [])
        self.lib.set_settings({"lyrics_sources": ["lrclib"]})

        def boom(*a):
            raise OSError("network down")
        with self.assertRaises(RuntimeError):
            L.fetch(self.lib, self.row["id"], client_factory=lambda: Client([("https://", boom)]), force=True)

    def test_offset_and_manual(self):
        from app.music import lyrics as L, api
        self.assertEqual(L.set_offset(self.lib, self.row["id"], 0.3)["offset"], 0.3)
        self.assertEqual(L.set_offset(self.lib, self.row["id"], 99)["offset"], 30.0)
        L.set_manual(self.lib, self.row["id"], "[00:01.00]<00:01.00>One <00:01.50>two\n[00:03.00]Three")
        res = L.fetch(self.lib, self.row["id"], client_factory=lambda: Client([]))
        self.assertEqual((res["source"], res["offset"], res["words"]), ("manual", 30.0, True))
        self.assertIn("lyrics.choose", api.OPS)
        self.assertIn("lyrics.offset", api.OPS)

class RemoteSpeed(unittest.TestCase):
    def test_position_extrapolates_with_speed(self):
        from app.music.player import PlayerHub
        hub = PlayerHub(None)
        hub.update({"track_id": "t", "position": 10, "playing": True, "speed": 2, "duration": 300, "device": "pc"})
        hub.state["position_at"] -= 5
        self.assertAlmostEqual(hub.snapshot()["position_now"], 20, delta=0.3)
        hub.update({"speed": 99})
        self.assertEqual(hub.state["speed"], 4.0)

class FrontSource(unittest.TestCase):
    def test_np_uses_track_time_not_css_clock(self):
        np = (MUSIC / "np.js").read_text(encoding="utf-8")
        css = (MUSIC / "style.css").read_text(encoding="utf-8")
        self.assertNotIn("--ld", np)
        self.assertNotIn("mm-karaoke var(--ld", css)
        self.assertIn("--lp", np)
        self.assertIn("lyrics.offset", np)
        self.assertIn('id="ly-bar"', (MUSIC / "index.html").read_text(encoding="utf-8"))
        i18n = json.loads((MUSIC / "i18n.json").read_text(encoding="utf-8"))
        for key in ("lyrics.wrong", "lyrics.offset", "lyrics.variants", "set.lyrics_sources", "set.lyrics_plain", "lyrics.off"):
            self.assertTrue(i18n["ru"].get(key) and i18n["en"].get(key), key)
        self.assertIn("pc.speed", (ROOT / "app" / "tgapp" / "music.js").read_text(encoding="utf-8"))

@unittest.skipUnless(os.environ.get("MEMU_TEST_CHROMIUM") and Path(os.environ.get("MEMU_TEST_CHROMIUM", "")).exists(),
                     "needs MEMU_TEST_CHROMIUM")
class KaraokeAtSpeed(unittest.TestCase):
    """np.js's syncLyrics in a page with a fake player: at rate 2 the active line and the fill follow currentTime."""

    def test_rate_two(self):
        from playwright.sync_api import sync_playwright
        src = (MUSIC / "np.js").read_text(encoding="utf-8")
        body = src[src.index("  function syncLyrics(cur, force) {"):src.index("  function positionLyrics() {")]
        timing_js = (MUSIC / "lyrics-sync.js").read_text(encoding="utf-8")
        page_html = """<div id="np"><div id="lyrics"><div id="lyrics-inner">%s</div></div></div><script>
          const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
          const el = $("#np"); let tab = "lyrics", lyricIdx = -2, wordIdx = -1;
          const Player = { st: { speed: 2 } };
          function positionLyrics() {}
          function lyricClock(cur) { return cur - lyrics.offset; }   // r41: the clock helper of np.js (paused player → currentTime)
          let lyrics = { synced: [[0, "a"], [2, "b"], [4, "c"], [6, "d", [[6, "w1 "], [7, "w2"]]], [8, "e"]], offset: 0 };
          %s
          // r41b: the state np.js keeps after rendering (dense lines: each one is sung up to the next stamp)
          let timing = LyricTiming.build(lyrics.synced, 10), lineNodes = $$("#lyrics-inner .mm-lyric"), gapNodes = new Map(), wordSpans = [], gapKey = "", gapEnd = false, syncShown = "smart";
          %s
          window.syncLyrics = syncLyrics; window.L = lyrics;
        </script>""" % ("".join(f'<button class="mm-lyric{" words" if i == 3 else ""}"><span>{"<i class=w>w1 </i><i class=w>w2</i>" if i == 3 else "x"}</span></button>' for i in range(5)), timing_js, body)
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=os.environ["MEMU_TEST_CHROMIUM"], args=["--no-sandbox"])
            try:
                page = browser.new_page()
                page.set_content(page_html)

                active = page.evaluate("""() => { const out = [];
                    for (const cur of [0.5, 2.9, 4.2, 5.0, 7.3, 9]) { syncLyrics(cur); out.push([$$(".mm-lyric").findIndex((n) => n.classList.contains("on")),
                      +getComputedStyle($$(".mm-lyric.on")[0]).getPropertyValue("--lp") || 0]); }
                    return out; }""")
                self.assertEqual([a for a, _ in active], [0, 1, 2, 2, 3, 4])
                self.assertAlmostEqual(active[3][1], (5.0 - 4) / 2, delta=0.02)
                words = page.evaluate("() => { syncLyrics(7.3); return $$('.mm-lyric.on .w').map((w) => w.className); }")
                self.assertEqual(words, ["w sung", "w on"])
                page.evaluate("() => { L.offset = 1; }")
                self.assertEqual(page.evaluate("() => { syncLyrics(7.3, true); return $$('.mm-lyric.on .w').map((w) => w.className); }"),
                                 ["w on", "w"])
            finally:
                browser.close()

if __name__ == "__main__":
    unittest.main()
