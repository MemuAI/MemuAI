# -*- coding: utf-8 -*-
"""Round 41 (r41b-online): MemuMusic online search — where a track comes from and the «Track info» sheet.

Backend (app/music/online.py) without network: a fake httpx client answers the YouTube Music web API / iTunes /
Deezer, a fake runner stands in for yt-dlp. Browser part: the real module in demo mode (round37_music_harness).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="r41b-online-"))

from app.music import online  # noqa: E402


def _run(text, page="", artist=False, album=False):
    run = {"text": text}
    if page:
        run["navigationEndpoint"] = {"browseEndpoint": {"browseId": "x", "browseEndpointContextSupportedConfigs": {
            "browseEndpointContextMusicConfig": {"pageType": page}}}}
    return run


def ytm_song(vid, title, artists, album, length, plays, explicit=False):
    meta = []
    for i, a in enumerate(artists):
        if i:
            meta.append({"text": ", "})
        meta.append(_run(a, "MUSIC_PAGE_TYPE_ARTIST"))
    meta += [{"text": " • "}, _run(album, "MUSIC_PAGE_TYPE_ALBUM"), {"text": " • "}, {"text": length}]
    col = lambda runs: {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": runs}}}
    item = {"playlistItemData": {"videoId": vid},
            "flexColumns": [col([{"text": title, "navigationEndpoint": {"watchEndpoint": {"videoId": vid}}}]), col(meta), col([{"text": plays}])],
            "thumbnail": {"musicThumbnailRenderer": {"thumbnail": {"thumbnails": [{"url": "https://lh3.googleusercontent.com/abc=w60-h60-l90-rj"}]}}}}
    if explicit:
        item["badges"] = [{"musicInlineBadgeRenderer": {"icon": {"iconType": "MUSIC_EXPLICIT_BADGE"}}}]
    return {"musicResponsiveListItemRenderer": item}


SEARCH_JSON = {"contents": {"tabbedSearchResultsRenderer": {"tabs": [{"content": {"sectionListRenderer": {"contents": [
    {"musicShelfRenderer": {"contents": [
        ytm_song("aaaaaaaaaa1", "One More Time", ["Daft Punk"], "Discovery", "5:21", "852M plays"),
        ytm_song("aaaaaaaaaa2", "Get Lucky (feat. Pharrell Williams)", ["Daft Punk", "Pharrell Williams"], "Random Access Memories", "6:09", "1.2B plays", True),
        ytm_song("aaaaaaaaaa3", "One More Time (Acoustic Cover)", ["Some Band"], "Covers", "3:40", "12K plays"),
    ]}}]}}}]}}}

NEXT_JSON = {"contents": {"playlistPanelRenderer": {"contents": [
    {"playlistPanelVideoRenderer": {"videoId": "aaaaaaaaaa1", "title": {"runs": [{"text": "One More Time"}]}}},
    {"playlistPanelVideoRenderer": {"videoId": "bbbbbbbbbb1", "title": {"runs": [{"text": "Around the World"}]},
                                    "longBylineText": {"runs": [{"text": "Daft Punk"}, {"text": " • "}, {"text": "Homework"}, {"text": " • "}, {"text": "1996"}]},
                                    "lengthText": {"runs": [{"text": "7:10"}]}, "thumbnail": {"thumbnails": [{"url": "https://i.ytimg.com/vi/b/hq.jpg"}]}}},
    {"playlistPanelVideoRenderer": {"videoId": "bbbbbbbbbb2", "title": {"runs": [{"text": "Lady"}]},
                                    "longBylineText": {"runs": [{"text": "Modjo"}, {"text": ", "}, {"text": "Someone"}, {"text": " • "}, {"text": "Modjo"}, {"text": " • "}, {"text": "2001"}]},
                                    "lengthText": {"runs": [{"text": "3:58"}]}}},
]}}}


class Resp:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return self.data


class FakeClient:
    calls: list = []
    fail_ytm = False

    def __init__(self):
        pass

    def post(self, url, json=None, headers=None):
        FakeClient.calls.append(("POST", url, json))
        if FakeClient.fail_ytm:
            return Resp({}, 403)
        return Resp(SEARCH_JSON if "/search" in url else NEXT_JSON)

    def get(self, url):
        FakeClient.calls.append(("GET", url, None))
        if "itunes.apple.com" in url:
            return Resp({"results": [{"trackName": "One More Time", "artistName": "Daft Punk", "collectionName": "Discovery",
                                      "releaseDate": "2001-03-12T08:00:00Z", "primaryGenreName": "Electronic",
                                      "artworkUrl100": "https://is1.mzstatic.com/a/100x100bb.jpg", "trackNumber": 1}]})
        return Resp({"data": []})

    def close(self):
        pass


class FakeRunner:
    targets: list = []
    full: dict = {}

    def json(self, target, flat=True, limit=0, timeout=90):
        FakeRunner.targets.append((target, flat))
        if target.startswith("scsearch"):
            return {"entries": [
                {"id": "1", "title": "One More Time", "track": "One More Time", "uploader": "Daft Punk", "uploader_url": "https://soundcloud.com/daftpunk",
                 "webpage_url": "https://soundcloud.com/daftpunk/one-more-time", "url": "https://api.soundcloud.com/tracks/1", "duration": 30.0,
                 "view_count": 3483256, "like_count": 92042, "timestamp": 1458650817, "artists": ["Daft Punk"]},
                {"id": "2", "title": "One More Time (Live)", "uploader": "fan", "webpage_url": "https://soundcloud.com/fan/omt-live", "duration": 330.0}]}
        if target.startswith("ytsearch"):
            return {"entries": [
                {"id": "FGBhQbmPwH8", "title": "Daft Punk - One More Time (Official Video)", "channel": "Daft Punk", "channel_is_verified": True,
                 "duration": 322.0, "view_count": 620856384, "url": "https://www.youtube.com/watch?v=FGBhQbmPwH8"},
                {"id": "xxxxxxxxxx1", "title": "One More Time", "channel": "Daft Punk - Topic", "duration": 321.0},
                {"id": "xxxxxxxxxx2", "title": "Daft Punk - One More Time (Slowed + Reverb)", "channel": "lofi guy", "duration": 400.0}]}
        if target.startswith("https://music.youtube.com/search"):
            return {"entries": [{"id": "wU26xVT_vBU", "title": "One More Time (Radio Edit)", "url": "https://music.youtube.com/watch?v=wU26xVT_vBU"}]}
        if target.endswith("/recommended"):
            return {"entries": [{"id": "9", "title": "Around the World", "uploader": "Daft Punk", "webpage_url": "https://soundcloud.com/daftpunk/atw", "duration": 400.0}]}
        if FakeRunner.full is None:
            return None   # «--ignore-errors» → null: YouTube «not a bot»
        return FakeRunner.full


FULL_INFO = {"id": "aaaaaaaaaa1", "title": "One More Time", "description": "Official audio\nDiscovery (2001)", "upload_date": "20180307",
             "like_count": 4100000, "view_count": 852000000, "channel": "Daft Punk", "channel_follower_count": 9800000, "channel_is_verified": True,
             "album": "Discovery", "artist": "Daft Punk", "track": "One More Time", "release_year": 2001, "duration": 321,
             "formats": [{"format_id": "251", "ext": "webm", "acodec": "opus", "vcodec": "none", "abr": 135.2, "asr": 48000, "filesize": 5300000},
                         {"format_id": "140", "ext": "m4a", "acodec": "mp4a.40.2", "vcodec": "none", "abr": 129.5, "asr": 44100, "filesize": 5200000},
                         {"format_id": "18", "ext": "mp4", "acodec": "mp4a.40.2", "vcodec": "avc1", "abr": 96}]}


class FakeLib:
    def __init__(self, by_source=None, similar=None, settings=None):
        self.by_source, self.similar_rows, self._settings = by_source or {}, similar or [], settings or {}

    def find_by_source(self, key):
        return self.by_source.get(key)

    def find_similar(self, title, artist, duration=0):
        return next((r for r in self.similar_rows if r["title"] == title and r["artist"] == artist), None)

    def settings(self):
        return self._settings


class OnlineBackendTest(unittest.TestCase):
    def setUp(self):
        online.clear_cache()
        FakeClient.calls, FakeClient.fail_ytm = [], False
        FakeRunner.targets, FakeRunner.full = [], dict(FULL_INFO)

    def search(self, source, lib=None):
        return online.search(lib or FakeLib(), "one more time", 10, source, client_factory=FakeClient, runner_factory=FakeRunner)

    def test_ytmusic_web_api_gives_artist_album_duration_plays(self):
        res = self.search("ytmusic")
        self.assertEqual(res["via"], "ytmusic-api")
        a, b, c = res["results"]
        self.assertEqual((a["title"], a["artist"], a["album"], a["duration"], a["views"]), ("One More Time", "Daft Punk", "Discovery", 321.0, 852000000))
        self.assertEqual(a["source"], "ytmusic")
        self.assertEqual(a["url"], "https://music.youtube.com/watch?v=aaaaaaaaaa1")
        self.assertIn("w544-h544", a["thumb"])
        self.assertEqual(a["tags"], ["official_audio"])
        self.assertEqual(b["artist"], "Daft Punk, Pharrell Williams")
        self.assertTrue(b["explicit"])
        self.assertEqual(b["views"], 1200000000)
        self.assertIn("cover", c["tags"])
        self.assertNotIn("official_audio", c["tags"])   # a cover is not the label's audio
        self.assertEqual(FakeRunner.targets, [])   # no yt-dlp needed

    def test_ytmusic_falls_back_to_ytdlp(self):
        FakeClient.fail_ytm = True
        res = self.search("ytmusic")
        self.assertEqual(res["via"], "yt-dlp")
        self.assertEqual(res["results"][0]["url"], "https://music.youtube.com/watch?v=wU26xVT_vBU")
        self.assertNotIn("remix", res["results"][0]["tags"])   # «Radio Edit» is not a remix

    def test_youtube_channel_verified_and_kind(self):
        a, b, c = self.search("youtube")["results"]
        self.assertEqual((a["artist"], a["title"], a["channel"], a["verified"]), ("Daft Punk", "One More Time", "Daft Punk", True))
        self.assertEqual(a["tags"], ["official_video"])
        self.assertEqual(b["tags"], ["official_audio"])   # «- Topic» channel
        self.assertIn("slowed", c["tags"])

    def test_soundcloud_preview_likes_date(self):
        a, b = self.search("soundcloud")["results"]
        self.assertEqual(a["url"], "https://soundcloud.com/daftpunk/one-more-time")   # not the api.soundcloud URL
        self.assertTrue(a["preview"])
        self.assertIn("preview", a["tags"])
        self.assertEqual((a["likes"], a["published"], a["channel_url"]), (92042, "2016-03-22", "https://soundcloud.com/daftpunk"))
        self.assertIn("live", b["tags"])
        self.assertFalse(b["preview"])

    def test_in_library_by_source_key_or_similarity(self):
        lib = FakeLib(by_source={"yt:aaaaaaaaaa1": {"id": "t1"}}, similar=[{"id": "t2", "title": "Get Lucky (feat. Pharrell Williams)", "artist": "Daft Punk, Pharrell Williams"}])
        res = self.search("ytmusic", lib)
        self.assertEqual([r["in_library"] for r in res["results"]], ["t1", "t2", ""])

    def test_search_is_cached_and_failures_are_not(self):
        self.search("youtube"); self.search("youtube")
        self.assertEqual(len([t for t in FakeRunner.targets if t[0].startswith("ytsearch")]), 1)

        class Broken(FakeRunner):
            def json(self, *a, **k):
                raise RuntimeError("network down")
        res = online.search(FakeLib(), "zzz", 5, "youtube", runner_factory=Broken)
        self.assertEqual(res["results"], [])
        self.assertIn("network down", res["error"])
        self.assertNotIn(("search", "youtube", "zzz", 5), online._cache)

    def test_details_merges_ytdlp_and_itunes(self):
        hint = {"title": "One More Time", "artist": "Daft Punk", "source": "ytmusic", "duration": 321}
        d = online.details(FakeLib(), "https://music.youtube.com/watch?v=aaaaaaaaaa1", hint, client_factory=FakeClient, runner_factory=FakeRunner)
        self.assertEqual((d["album"], d["year"], d["genre"]), ("Discovery", "2001", "Electronic"))
        self.assertEqual((d["published"], d["likes"], d["followers"]), ("2018-03-07", 4100000, 9800000))
        self.assertEqual([(f["codec"], f["kbps"]) for f in d["formats"]], [("opus", 135), ("aac", 130)])   # audio only, best first
        self.assertEqual(d["formats"][0]["khz"], 48.0)
        self.assertTrue(d["cover"].endswith("1000x1000bb.jpg"))
        self.assertEqual(d["meta_source"], "itunes")
        self.assertEqual(d["errors"], {})
        self.assertTrue(d["related"])
        self.assertIn("Discovery (2001)", d["description"])

    def test_details_graceful_when_source_blocks(self):
        FakeRunner.full = None   # YouTube «not a bot»: yt-dlp answers null
        d = online.details(FakeLib(), "https://www.youtube.com/watch?v=FGBhQbmPwH8", {"title": "One More Time", "artist": "Daft Punk", "source": "youtube", "views": 5},
                           client_factory=FakeClient, runner_factory=FakeRunner)
        self.assertEqual(d["errors"], {"info": "blocked"})
        self.assertEqual((d["album"], d["year"], d["views"]), ("Discovery", "2001", 5))   # iTunes and the hint still answer
        self.assertEqual(d["formats"], [])
        with self.assertRaises(ValueError):
            online.details(FakeLib(), "not a link", {})

    def test_related_youtube_radio_and_soundcloud(self):
        rel = online.related(FakeLib(), "https://music.youtube.com/watch?v=aaaaaaaaaa1", 10, client_factory=FakeClient)
        self.assertEqual([(r["title"], r["artist"], r["album"], r["year"], r["duration"]) for r in rel],
                         [("Around the World", "Daft Punk", "Homework", "1996", 430.0), ("Lady", "Modjo, Someone", "Modjo", "2001", 238.0)])
        body = [c for c in FakeClient.calls if c[0] == "POST"][-1][2]
        self.assertEqual(body["playlistId"], "RDAMVMaaaaaaaaaa1")
        sc = online.related(FakeLib(), "https://soundcloud.com/daftpunk/one-more-time?in=x", 5, runner_factory=FakeRunner)
        self.assertEqual(sc[0]["title"], "Around the World")
        self.assertIn(("https://soundcloud.com/daftpunk/one-more-time/recommended", True), FakeRunner.targets)
        self.assertEqual(online.related(FakeLib(), "https://example.com/song.mp3", 5), [])

    def test_lyrics_lookup_uses_library_sources(self):
        from app.music import lyrics
        seen = {}

        def fake_search(q, sources, client_factory=None, plain_fallback=True):
            seen.update(q=q, sources=sources)
            return [{"source": "lrclib", "plain": "", "synced": [[1.0, "line one"], [2.0, "line two"]]}], {}
        orig = lyrics.search
        lyrics.search = fake_search
        try:
            ly = online.lyrics_lookup(FakeLib(settings={"lyrics_sources": ["lrclib", "nope"]}), "Daft Punk - One More Time", "Daft Punk VEVO", 320)
        finally:
            lyrics.search = orig
        self.assertEqual(seen["sources"], ["lrclib"])
        self.assertEqual(seen["q"]["title"], "One More Time")
        self.assertEqual((ly["found"], ly["synced"], ly["text"]), (True, True, "line one\nline two"))

    def test_api_ops_proxy_thumbs(self):
        from app.music import api
        self.assertTrue({"online.search", "online.details", "online.related", "online.lyrics"} <= set(api.OPS))
        orig = online.search
        online.search = lambda lib, q, limit, source: {"results": [{"title": "x", "thumb": "https://i.ytimg.com/vi/a/hq.jpg", "tags": []}], "source": source, "via": "t", "error": ""}
        try:
            res = api.call("online.search", {"q": "x", "source": "soundcloud"})
        finally:
            online.search = orig
        self.assertEqual(res["source"], "soundcloud")
        self.assertTrue(res["results"][0]["thumb"].startswith("/api/pg/music/thumb?u="))


class OnlineUiTest(unittest.TestCase):
    """The real module in demo mode: rows with source/tags, the sheet, source switch, «originals only», Esc."""

    @classmethod
    def setUpClass(cls):
        from round37_music_harness import chromium
        if not chromium():
            raise unittest.SkipTest("no Chromium")

    def test_online_rows_and_track_sheet(self):
        from playwright.sync_api import sync_playwright
        from round37_music_harness import frame_of, open_page, wait
        shots = os.environ.get("MEMU_R41B_SHOTS", "")
        with sync_playwright() as pw:
            browser, page, errors = open_page(pw, "ru", "dark", (1280, 820))
            try:
                f = frame_of(page)
                self.assertTrue(wait(page, f, "() => !!(window.MemuMusic && MemuMusic.S.loaded)"))
                f.evaluate("() => { MemuMusic.S.prefs.onlineSource = 'ytmusic'; MemuMusic.S.prefs.onlineOriginals = false; MemuMusic.go('search', { q: 'Glass Rivers' }, { instant: true }); }")
                page.wait_for_timeout(300)
                f.evaluate("() => document.querySelector('#gs-online').click()")
                self.assertTrue(wait(page, f, "() => document.querySelectorAll('.mm-orow').length === 8"))
                row = f.evaluate("""() => { const r = document.querySelector('.mm-orow');
                  return { src: r.querySelector('.mm-osrc').textContent, tags: [...document.querySelectorAll('.mm-orow .mm-otag')].map((x) => x.textContent),
                           inlib: !!r.querySelector('.mm-oinlib'), play: !!r.querySelector('[data-play]'), seg: document.querySelectorAll('.mm-oseg [role=tab]').length }; }""")
                self.assertIn("YouTube Music", row["src"])
                self.assertIn("прослушиваний", row["src"])
                self.assertTrue(row["inlib"] and row["play"])
                self.assertEqual(row["seg"], 3)
                for tag in ("Офиц. аудио", "Live", "Ремикс", "Кавер", "Замедлено"):
                    self.assertIn(tag, row["tags"])
                # «Originals only» hides covers / remixes / slowed
                f.evaluate("() => document.querySelector('[data-orig]').click()")
                self.assertTrue(wait(page, f, "() => document.querySelectorAll('.mm-orow').length === 5"))
                f.evaluate("() => document.querySelector('[data-orig]').click()")
                self.assertTrue(wait(page, f, "() => document.querySelectorAll('.mm-orow').length === 8"))
                # source switch is remembered
                f.evaluate("() => document.querySelector('.mm-oseg [data-src=soundcloud]').click()")
                self.assertTrue(wait(page, f, "() => document.querySelector('.mm-orow .mm-osrc') && document.querySelector('.mm-orow .mm-osrc').textContent.includes('SoundCloud')"))
                self.assertEqual(f.evaluate("() => MemuMusic.S.prefs.onlineSource"), "soundcloud")
                self.assertTrue(f.evaluate("() => [...document.querySelectorAll('.mm-otag')].some((x) => x.textContent === 'Превью 30 с')"))
                # the sheet
                f.evaluate("() => document.querySelectorAll('.mm-orow')[1].click()")
                self.assertTrue(wait(page, f, "() => document.querySelector('.mm-osheet.open') && document.querySelectorAll('.mm-ogrid dt').length >= 6"))
                sheet = f.evaluate("""() => { const s = document.querySelector('.mm-osheet-panel');
                  return { dt: [...s.querySelectorAll('.mm-ogrid dt')].map((x) => x.textContent), formats: s.querySelectorAll('.mm-oformats span').length,
                           desc: !!s.querySelector('.mm-odesc'), source: s.querySelector('.mm-osource').textContent,
                           acts: [...s.querySelectorAll('[data-a]')].map((x) => x.dataset.a), title: s.querySelector('h2').textContent }; }""")
                for k in ("Альбом", "Год", "Жанр", "Длительность", "Опубликовано", "Лайки", "Лучшее качество"):
                    self.assertIn(k, sheet["dt"])
                self.assertEqual(sheet["formats"], 2)
                self.assertTrue(sheet["desc"])
                self.assertIn("SoundCloud", sheet["source"])
                for a in ("listen", "dl", "queue", "pl", "copy", "open", "lyrics"):
                    self.assertIn(a, sheet["acts"])
                self.assertTrue(wait(page, f, "() => document.querySelectorAll('.mm-osim').length === 6"))   # similar, lazily
                f.evaluate("() => document.querySelector('[data-a=lyrics]').click()")
                self.assertTrue(wait(page, f, "() => !!document.querySelector('.mm-otext')"))
                f.evaluate("() => document.querySelector('[data-a=more-desc]').click()")
                self.assertTrue(f.evaluate("() => document.querySelector('.mm-odesc').hasAttribute('data-open')"))
                if shots:
                    Path(shots).mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(Path(shots) / "test-sheet.png"))
                # Listen on a track that is not in the library → download.start + «will start once downloaded»; Esc closes
                f.evaluate("() => document.querySelector('[data-a=listen]').click()")
                self.assertTrue(wait(page, f, "() => [...document.querySelectorAll('.mm-toast')].some((x) => x.textContent.includes('включится'))"))
                f.evaluate("() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))")
                self.assertTrue(wait(page, f, "() => document.querySelector('.mm-osheet').hidden"))
                # the Download screen uses the same rows
                f.evaluate("() => { MemuMusic.S.prefs.onlineSource = 'youtube'; MemuMusic.go('download', {}, { instant: true }); }")
                page.wait_for_timeout(300)
                f.evaluate("() => { const q = document.querySelector('#dl-q'); q.value = 'Night'; q.dispatchEvent(new Event('input', { bubbles: true })); }")
                self.assertTrue(wait(page, f, "() => document.querySelectorAll('#dl-results .mm-orow').length === 8"))
                self.assertTrue(f.evaluate("() => document.querySelector('#dl-results .mm-osrc').textContent.includes('YouTube')"))
                self.assertEqual([e for e in errors if "favicon" not in e], [])
            finally:
                browser.close()


class OnlineStaticTest(unittest.TestCase):
    def test_parts_strings_and_no_heavy_patterns(self):
        music = ROOT / "app" / "module_builtins" / "music"
        self.assertIn("/* @memu-part:feat-online.js */", (music / "main.js").read_text(encoding="utf-8"))
        self.assertIn("/* @memu-part:feat-online.css */", (music / "style.css").read_text(encoding="utf-8"))
        js = (music / "feat-online.js").read_text(encoding="utf-8")
        css = (music / "feat-online.css").read_text(encoding="utf-8")
        self.assertNotIn("setInterval", js)
        self.assertNotIn("MutationObserver", js)
        self.assertNotIn("backdrop-filter", css, "no backdrop blur")
        i18n = json.loads((music / "i18n.json").read_text(encoding="utf-8"))
        import re
        keys = {k for k in re.findall(r"t\(\"(onl\.[a-z_.]+)\"", js) if not k.endswith(".")}
        keys |= {"onl.tag." + k for k in ("official_audio", "official_video", "lyric", "live", "cover", "remix", "slowed", "sped_up", "karaoke", "instrumental", "extended", "acoustic", "visualizer", "preview")}
        keys |= {"onl.err." + k for k in ("blocked", "bot", "unavailable", "timeout", "failed")}
        keys |= {"onl.f." + k for k in ("album", "year", "genre", "duration", "published", "plays", "views_n", "likes", "quality")}
        for k in keys:
            self.assertIn(k, i18n["ru"], k)
            self.assertIn(k, i18n["en"], k)
        self.assertNotIn("function onlineSearch", (music / "feat-extra.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
