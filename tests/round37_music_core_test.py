# -*- coding: utf-8 -*-
"""Round 37: MemuMusic backend (app/music/) — no network: yt-dlp, HTTP and LRCLIB are faked."""
from __future__ import annotations

import io
import json
import re
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class FakeResponse:
    def __init__(self, status=200, data=None, text="", content=b"", url=""):
        self.status_code = status
        self._data = data
        self.text = text if text else (json.dumps(data) if data is not None else "")
        self.content = content or self.text.encode()
        self.url = url

    def json(self):
        return self._data if self._data is not None else json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, **kw):
        self.calls.append((url, params))
        for prefix, response in self.routes:
            if url.startswith(prefix):
                return response(url, params) if callable(response) else response
        return FakeResponse(404, {"error": "nf"})

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def png_bytes(color=(200, 40, 60), size=(64, 36)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.data = Path(self._t.name)
        self._env = patch.dict(os.environ, {"MEMUAI_DATA_DIR": str(self.data)})
        self._env.start()
        from app.music.library import get_library
        self.lib = get_library()

    def tearDown(self):
        from app.music import library
        self.lib.close()
        library._LIBS.clear()
        self._env.stop()
        self._t.cleanup()

    def make_file(self, name="a.m4a", size=5000):
        path = self.lib.tracks_dir / name
        path.write_bytes(bytes(range(256)) * (size // 256) + b"x" * (size % 256))
        return path


class DetectTest(unittest.TestCase):
    def test_sources(self):
        from app.music.sources import detect
        cases = {
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ": ("youtube", "track", "dQw4w9WgXcQ"),
            "https://youtu.be/dQw4w9WgXcQ?si=x": ("youtube", "track", "dQw4w9WgXcQ"),
            "https://youtube.com/shorts/abcdEFGhijk": ("youtube", "track", "abcdEFGhijk"),
            "https://www.youtube.com/playlist?list=PL123": ("youtube", "playlist", "PL123"),
            "https://music.youtube.com/watch?v=Rgrt_8mXrK8&list=RD": ("ytmusic", "track", "Rgrt_8mXrK8"),
            "https://music.youtube.com/playlist?list=OLAK5uy_abc": ("ytmusic", "album", "OLAK5uy_abc"),
            "https://music.youtube.com/playlist?list=PLxyz": ("ytmusic", "playlist", "PLxyz"),
            "https://open.spotify.com/intl-de/track/4cOdK2wGLETKBW3PvgPWqT?si=1": ("spotify", "track", "4cOdK2wGLETKBW3PvgPWqT"),
            "https://open.spotify.com/album/4m2880jivSbbyEGAKfITCa": ("spotify", "album", "4m2880jivSbbyEGAKfITCa"),
            "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M": ("spotify", "playlist", "37i9dQZF1DXcBWIGoYBM5M"),
            "https://music.apple.com/us/album/random-access-memories/617154241": ("apple", "album", "617154241"),
            "https://music.apple.com/us/album/get-lucky/617154241?i=617154366": ("apple", "track", "617154366"),
            "https://music.apple.com/ru/playlist/top/pl.u-abc": ("apple", "playlist", "pl.u-abc"),
            "https://soundcloud.com/forss/flickermood": ("soundcloud", "track", "forss/flickermood"),
            "https://soundcloud.com/forss/sets/soulhack": ("soundcloud", "playlist", "forss/sets/soulhack"),
            "https://soundcloud.com/forss": ("soundcloud", "artist", "forss"),
            "https://www.deezer.com/en/track/3135556": ("deezer", "track", "3135556"),
            "https://www.deezer.com/album/302127": ("deezer", "album", "302127"),
            "https://music.yandex.ru/album/4766/track/57703": ("yandex", "track", "57703"),
            "https://music.yandex.ru/album/4766": ("yandex", "album", "4766"),
            "https://music.yandex.ru/users/music-blog/playlists/2121": ("yandex", "playlist", "music-blog:2121"),
            "https://tidal.com/browse/track/77646168": ("tidal", "track", "77646168"),
            "https://music.amazon.com/albums/B00C6MPWVQ?trackAsin=B00C6MPX3A": ("amazon", "track", "B00C6MPX3A"),
            "https://daftpunk.bandcamp.com/album/ram": ("bandcamp", "album", ""),
            "https://vimeo.com/76979871": ("vimeo", "track", ""),
            "https://www.mixcloud.com/someone/mix-1/": ("mixcloud", "track", ""),
            "https://example.org/song.mp3": ("web", "track", ""),
            "daft punk get lucky": ("search", "query", ""),
        }
        for url, (source, kind, item_id) in cases.items():
            info = detect(url)
            self.assertEqual((info["source"], info["kind"]), (source, kind), url)
            if item_id:
                self.assertEqual(info["id"], item_id, url)
        self.assertTrue(detect("https://soundcloud.com/a/b")["direct"])
        self.assertFalse(detect("https://open.spotify.com/track/x1")["direct"])
        self.assertTrue(detect("https://spotify.link/abc")["short"])


class LibraryTest(Base):
    def test_tracks_playlists_stats(self):
        lib = self.lib
        a = lib.add({"file": str(self.make_file("a.m4a")), "title": "Song A", "artist": "Band", "album": "LP",
                     "duration": 200, "source": "youtube", "track_no": 2}, png_bytes((220, 30, 40)))
        b = lib.add({"file": str(self.make_file("b.mp3")), "title": "Song B", "artist": "Band", "album": "LP",
                     "duration": 100, "source": "local", "track_no": 1})
        c = lib.add({"file": str(self.make_file("c.opus")), "title": "Other", "artist": "Solo", "duration": 50})
        listed = lib.list()
        self.assertEqual(listed["total"], 3)
        self.assertEqual(listed["tracks"][0]["id"], c["id"])          # newest first
        track = lib.get(a["id"])
        self.assertTrue(track["cover"].startswith(f"/api/pg/music/cover/{a['id']}?t="))
        self.assertTrue(track["stream"].startswith(f"/api/pg/music/stream/{a['id']}?t="))
        self.assertRegex(track["accent"], r"^#[0-9a-f]{6}$")
        self.assertGreater(int(track["accent"][1:3], 16), 150)       # the red cover → a red accent
        self.assertNotIn("file", track)
        self.assertEqual(lib.list(q="other")["total"], 1)
        self.assertEqual(lib.list(q="song band")["total"], 2)
        self.assertEqual(lib.list(filter="downloaded")["total"], 1)
        self.assertEqual([t["title"] for t in lib.list(sort="album")["tracks"]], ["Other", "Song B", "Song A"])
        lib.like(b["id"], True)
        self.assertEqual(lib.list(filter="liked")["tracks"][0]["id"], b["id"])
        self.assertTrue(lib.played(a["id"], 120)["counted"])
        self.assertTrue(lib.played(a["id"], 199)["counted"])
        self.assertFalse(lib.played(a["id"], 5)["counted"])
        self.assertTrue(lib.played(c["id"], 30)["counted"])
        stats = lib.stats()
        self.assertEqual((stats["tracks"], stats["artists"], stats["liked"]), (3, 2, 1))
        self.assertEqual(stats["top_tracks"][0]["id"], a["id"])
        self.assertEqual(stats["top_artists"][0]["name"], "Band")
        self.assertEqual(len(lib.history()), 4)
        # artists / albums
        self.assertEqual([x["name"] for x in lib.artists()], ["Band", "Solo"])
        albums = lib.albums()
        self.assertEqual(len(albums), 1)
        album = lib.album(albums[0]["key"])
        self.assertEqual([t["title"] for t in album["items"]], ["Song B", "Song A"])   # by track number
        self.assertEqual(len(lib.artist("band")["tracks"]), 2)
        # playlists
        smart = {p["id"]: p for p in lib.playlists()}
        self.assertEqual(smart["smart:liked"]["count"], 1)
        self.assertEqual(smart["smart:top"]["count"], 2)
        pl = lib.create_playlist("Road", [a["id"], "nope", a["id"], b["id"]])
        self.assertEqual([t["id"] for t in pl["tracks"]], [a["id"], b["id"]])
        pl = lib.update_playlist(pl["id"], {"add": [c["id"]], "remove": [a["id"]], "name": "Trip"})
        self.assertEqual((pl["name"], [t["id"] for t in pl["tracks"]]), ("Trip", [b["id"], c["id"]]))
        pl = lib.update_playlist(pl["id"], {"order": [c["id"], b["id"]]})
        self.assertEqual([t["id"] for t in pl["tracks"]], [c["id"], b["id"]])
        with self.assertRaises(ValueError):
            lib.update_playlist("smart:liked", {"name": "x"})
        # edit / delete
        self.assertEqual(lib.update(c["id"], {"title": "Renamed", "file": "/etc/passwd"})["title"], "Renamed")
        self.assertEqual(lib.row(c["id"])["file"], "c.opus")
        result = lib.delete(a["id"])
        self.assertTrue(result["file_removed"])
        self.assertFalse((lib.tracks_dir / "a.m4a").exists())
        self.assertEqual(lib.playlist(pl["id"])["count"], 2)
        lib.delete_playlist(pl["id"])
        with self.assertRaises(KeyError):
            lib.playlist(pl["id"])

    def test_imported_file_is_never_deleted_and_settings(self):
        outside = self.data / "outside"
        outside.mkdir()
        f = outside / "Artist - Title.mp3"
        f.write_bytes(b"ID3" + b"0" * 100)
        row = self.lib.add({"file": str(f), "title": "Title"})
        self.assertFalse(self.lib.delete(row["id"])["file_removed"])
        self.assertTrue(f.exists())
        with self.assertRaises(ValueError):
            self.lib.set_settings({"quality": "ultra"})
        with self.assertRaises(ValueError):
            self.lib.set_settings({"download_dir": "relative/path"})
        saved = self.lib.set_settings({"quality": "small", "unknown": 1, "cookies_from_browser": "edge"})
        self.assertEqual((saved["quality"], saved["cookies_from_browser"]), ("small", "edge"))
        self.assertNotIn("unknown", self.lib.settings())

    def test_tokens(self):
        token = self.lib.token("stream", "abcdefabcdef")
        self.assertTrue(self.lib.check_token("stream", "abcdefabcdef", token))
        self.assertFalse(self.lib.check_token("cover", "abcdefabcdef", token))
        self.assertFalse(self.lib.check_token("stream", "abcdefabcdef", ""))


class FakeRunner:
    """Pretends to be yt-dlp: writes a file and returns the after_move JSON."""
    def __init__(self, manager_dir, fail_urls=(), flat=None, delay=0.0):
        self.dir = manager_dir
        self.fail_urls = set(fail_urls)
        self.flat = flat or {}
        self.downloads = []
        self.delay = delay

    def json(self, target, flat=True, limit=0, timeout=90):
        if target in self.flat:
            return self.flat[target]
        if target.startswith("https://music.youtube.com/search"):
            return {"entries": [{"id": "matchedID01"}]}
        if target.startswith("scsearch"):
            return {"entries": [{"url": "https://api.soundcloud.com/tracks/1", "webpage_url": "https://soundcloud.com/x/song",
                                 "title": "Song", "duration": 200}]}
        return {"entries": []}

    def download(self, url, fmt, outdir, embed, on_progress, cancel, holder, cookies_browser=""):
        self.downloads.append((url, fmt))
        for step in range(5):
            if cancel.is_set():
                from app.music.downloader import Cancelled
                raise Cancelled()
            on_progress(step / 4)
            if self.delay:
                time.sleep(self.delay)
        if url in self.fail_urls:
            raise RuntimeError("ERROR: [youtube] x: Sign in to confirm you're not a bot")
        vid = re.sub(r"\W", "", url.rsplit("=", 1)[-1])[-11:]
        path = Path(outdir) / f"file-{vid}-{len(self.downloads)}.m4a"
        path.write_bytes(b"\0" * 2048)
        return {"id": vid, "title": "Rick Astley - Never Gonna Give You Up (Official Video)", "uploader": "Rick Astley",
                "duration": 213, "thumbnail": "https://i.ytimg.com/vi/x/maxres.jpg", "webpage_url": url, "abr": 129.5,
                "ext": "m4a", "filepath": str(path)}


class DownloaderTest(Base):
    def manager(self, runner, routes=None):
        from app.music.downloader import Manager
        client = FakeClient(routes or [("https://i.ytimg.com/", FakeResponse(200, content=png_bytes(), text="img"))])
        return Manager(library_factory=lambda: self.lib, runner_factory=lambda: runner, client_factory=lambda: client)

    def run_job(self, mgr, url, quality=""):
        from app.music.downloader import Job
        from app.music.sources import detect
        job = Job(url, "download", quality, detect(url))
        mgr.jobs[job.job_id] = job
        mgr.run(job)
        return job

    def test_direct_track_and_dedup(self):
        runner = FakeRunner(self.lib.tracks_dir)
        mgr = self.manager(runner)
        job = self.run_job(mgr, "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertEqual(job.status, "done", job.error)
        self.assertEqual(job.progress, 1.0)
        track = self.lib.get(job.track_ids[0])
        self.assertEqual((track["title"], track["artist"], track["source"]), ("Never Gonna Give You Up", "Rick Astley", "youtube"))
        self.assertTrue(track["cover"])
        self.assertEqual(track["bitrate"], 129)
        self.assertIn("bestaudio[ext=m4a]", runner.downloads[0][1])
        again = self.run_job(mgr, "https://youtu.be/dQw4w9WgXcQ")
        self.assertEqual(again.items[0]["status"], "exists")
        self.assertEqual(len(runner.downloads), 1)
        self.assertEqual(again.track_ids, job.track_ids)

    def test_playlist_partial_failure_and_retry(self):
        flat = {"https://www.youtube.com/playlist?list=PL1": {"title": "Mix", "entries": [
            {"url": "https://www.youtube.com/watch?v=aaaaaaaaaa1", "title": "A"},
            {"url": "https://www.youtube.com/watch?v=bbbbbbbbbb2", "title": "B"}, None]}}
        runner = FakeRunner(self.lib.tracks_dir, fail_urls={"https://www.youtube.com/watch?v=bbbbbbbbbb2"}, flat=flat)
        mgr = self.manager(runner)
        job = self.run_job(mgr, "https://www.youtube.com/playlist?list=PL1")
        self.assertEqual(job.title, "Mix")
        self.assertEqual(job.status, "done")
        self.assertEqual([i["status"] for i in job.items], ["done", "error"])
        self.assertIn("VPN", job.items[1]["error"])
        runner.fail_urls.clear()
        mgr.retry(job.job_id)
        for _ in range(100):
            if job.status not in ("queued", "running"):
                break
            time.sleep(0.05)
        self.assertEqual([i["status"] for i in job.items], ["done", "done"])
        self.assertEqual(len(job.track_ids), 2)
        self.assertEqual(len(runner.downloads), 3)

    def test_spotify_meta_match_and_soundcloud_fallback(self):
        meta = {"kind": "track", "title": "Song", "artist": "Band", "cover_url": "https://i.scdn.co/cover.jpg",
                "items": [{"title": "Song", "artist": "Band", "album": "LP", "duration": 200,
                           "cover_url": "https://i.scdn.co/cover.jpg", "source_url": "https://open.spotify.com/track/abc"}]}
        runner = FakeRunner(self.lib.tracks_dir, fail_urls={"https://music.youtube.com/watch?v=matchedID01"})
        routes = [("https://i.scdn.co/", FakeResponse(200, content=png_bytes((10, 200, 30)), text="img"))]
        mgr = self.manager(runner, routes)
        with patch("app.music.sources.resolve_meta", return_value=meta):
            job = self.run_job(mgr, "https://open.spotify.com/track/abc")
        self.assertEqual(job.status, "done", job.error)
        self.assertEqual([u for u, _ in runner.downloads], ["https://music.youtube.com/watch?v=matchedID01",
                                                             "https://soundcloud.com/x/song"])
        track = self.lib.get(job.track_ids[0])
        # The original Spotify metadata and cover win over what YouTube/SoundCloud said.
        self.assertEqual((track["title"], track["artist"], track["album"], track["source"]), ("Song", "Band", "LP", "spotify"))
        self.assertEqual(track["source_url"], "https://open.spotify.com/track/abc")
        self.assertGreater(int(track["accent"][3:5], 16), 150)
        with patch("app.music.sources.resolve_meta", return_value=meta):
            again = self.run_job(mgr, "https://open.spotify.com/track/abc")
        self.assertEqual(again.items[0]["status"], "exists")

    def test_cancel_and_queue(self):
        runner = FakeRunner(self.lib.tracks_dir, delay=0.2)
        mgr = self.manager(runner)
        started = mgr.start("https://www.youtube.com/watch?v=ccccccccccc")
        self.assertEqual(mgr.start("https://www.youtube.com/watch?v=ccccccccccc")["job_id"], started["job_id"])
        time.sleep(0.3)
        mgr.cancel(started["job_id"])
        for _ in range(50):
            if mgr.jobs[started["job_id"]].status not in ("queued", "running"):
                break
            time.sleep(0.1)
        self.assertEqual(mgr.jobs[started["job_id"]].status, "cancelled")
        self.assertEqual(self.lib.list()["total"], 0)
        listed = mgr.list()
        self.assertEqual(listed[0]["job_id"], started["job_id"])
        self.assertIn("items", listed[0])
        with self.assertRaises(ValueError):
            mgr.start("")

    def test_import_folder(self):
        folder = self.data / "Music" / "Album"
        folder.mkdir(parents=True)
        (folder / "01 - Band - First.mp3").write_bytes(b"\0" * 300)
        (folder / "Second.flac").write_bytes(b"\0" * 300)
        (folder / "notes.txt").write_text("x")
        (folder / "cover.jpg").write_bytes(png_bytes((30, 30, 220)))
        from app.music.downloader import Job
        mgr = self.manager(FakeRunner(self.lib.tracks_dir))
        job = Job(str(folder.parent), "import", info={"source": "local", "kind": "folder"})
        mgr.run(job)
        self.assertEqual(job.status, "done", job.error)
        titles = sorted((t["title"], t["artist"]) for t in self.lib.list()["tracks"])
        self.assertEqual(titles[0], ("First", "Band"))
        self.assertEqual(titles[1][0], "Second")
        self.assertTrue(all(t["cover"] for t in self.lib.list()["tracks"]))
        job2 = Job(str(folder.parent), "import", info={"source": "local", "kind": "folder"})
        mgr.run(job2)
        self.assertEqual({i["status"] for i in job2.items}, {"exists"})

    def test_split_title_and_errors(self):
        from app.music.downloader import friendly_error, split_title, format_for
        self.assertEqual(split_title("Daft Punk - Get Lucky (Official Audio)", "Daft Punk"),
                         {"artist": "Daft Punk", "title": "Get Lucky"})
        self.assertEqual(split_title("Get Lucky", "Daft Punk - Topic"), {"artist": "Daft Punk", "title": "Get Lucky"})
        self.assertIn("VPN", friendly_error("ERROR: [youtube] x: Sign in to confirm you’re not a bot."))
        self.assertEqual(format_for("best", False), "bestaudio/best")


class SourcesMetaTest(unittest.TestCase):
    def test_spotify_album_embed(self):
        from app.music import sources
        entity = {"type": "album", "name": "RAM", "title": "RAM", "subtitle": "Daft Punk",
                  "visualIdentity": {"image": [{"url": "https://i/small", "maxWidth": 64}, {"url": "https://i/big", "maxWidth": 640}]},
                  "trackList": [{"uri": "spotify:track:t1", "title": "Give Life Back to Music", "subtitle": "Daft Punk", "duration": 275386},
                                {"uri": "spotify:track:t2", "title": "The Game of Love", "subtitle": "Daft Punk", "duration": 322146}]}
        page = ('<html><script id="__NEXT_DATA__" type="application/json">'
                + json.dumps({"props": {"pageProps": {"state": {"data": {"entity": entity}}}}}) + "</script></html>")
        client = FakeClient([("https://open.spotify.com/embed/album/", FakeResponse(200, text=page))])
        meta = sources.resolve_meta(sources.detect("https://open.spotify.com/album/abc123"), client)
        self.assertEqual((meta["kind"], meta["title"], meta["artist"], meta["cover_url"]), ("album", "RAM", "Daft Punk", "https://i/big"))
        self.assertEqual([(i["title"], i["album"], i["track_no"], round(i["duration"])) for i in meta["items"]],
                         [("Give Life Back to Music", "RAM", 1, 275), ("The Game of Love", "RAM", 2, 322)])
        self.assertEqual(meta["items"][0]["source_url"], "https://open.spotify.com/track/t1")
        self.assertEqual(sources.search_query({"title": "Get Lucky (feat. Pharrell)", "artist": "Daft Punk, Pharrell"}),
                         "Daft Punk - Get Lucky")

    def test_apple_deezer_and_generic(self):
        from app.music import sources
        itunes = {"results": [{"wrapperType": "collection", "collectionName": "RAM", "artistName": "Daft Punk",
                               "artworkUrl100": "https://is1/x/100x100bb.jpg"},
                              {"wrapperType": "track", "trackName": "Get Lucky", "artistName": "Daft Punk",
                               "collectionName": "RAM", "trackTimeMillis": 369000, "trackNumber": 8,
                               "artworkUrl100": "https://is1/x/100x100bb.jpg"}]}
        deezer = {"id": 1, "title": "Harder", "artist": {"name": "Daft Punk"}, "duration": 224,
                  "album": {"title": "Discovery", "cover_xl": "https://dz/xl.jpg"}, "link": "https://deezer.com/track/1"}
        page = ('<html><head><meta property="og:title" content="Song Name - Single by Some Artist | TIDAL">'
                '<meta property="og:image" content="https://t/img.jpg"></head></html>')
        client = FakeClient([("https://itunes.apple.com/lookup", FakeResponse(200, itunes)),
                             ("https://api.deezer.com/track/1", FakeResponse(200, deezer)),
                             ("https://tidal.com/", FakeResponse(200, text=page))])
        apple = sources.resolve_meta(sources.detect("https://music.apple.com/us/album/ram/617154241"), client)
        self.assertEqual((apple["title"], apple["items"][0]["title"], apple["items"][0]["track_no"]), ("RAM", "Get Lucky", 8))
        self.assertTrue(apple["cover_url"].endswith("/1000x1000bb.jpg"))
        dz = sources.resolve_meta(sources.detect("https://www.deezer.com/track/1"), client)
        self.assertEqual((dz["items"][0]["album"], dz["cover_url"]), ("Discovery", "https://dz/xl.jpg"))
        tidal = sources.resolve_meta(sources.detect("https://tidal.com/browse/track/77"), client)
        self.assertEqual((tidal["title"], tidal["artist"], tidal["cover_url"]), ("Song Name - Single", "Some Artist", "https://t/img.jpg"))


class LyricsTest(Base):
    def test_lrc_and_fetch(self):
        from app.music import lyrics
        self.assertEqual(lyrics.parse_lrc("[00:01.50]Hello\n[01:02.3][01:05]Twice\nnot a line"),
                         [[1.5, "Hello"], [62.3, "Twice"], [65.0, "Twice"]])
        row = self.lib.add({"file": str(self.make_file()), "title": "Never Gonna Give You Up (Official Video)",
                            "artist": "Rick Astley", "duration": 213})
        junk = {"plainLyrics": "probe", "syncedLyrics": "[00:00.00]probe", "duration": 214}
        good = [{"plainLyrics": "a\nb\nc\nd", "syncedLyrics": "[00:19.67]We're no strangers\n[00:23.5]You know",
                 "duration": 213}]
        self.lib.set_settings({"lyrics_sources": ["lrclib"]})   # r39: only LRCLIB here (round39_music_lyrics_test — all sources)
        client = FakeClient([("https://lrclib.net/api/get", FakeResponse(200, junk)),
                             ("https://lrclib.net/api/search", FakeResponse(200, good))])
        result = lyrics.fetch(self.lib, row["id"], client_factory=lambda: client)
        self.assertEqual(result["synced"][0], [19.67, "We're no strangers"])
        self.assertEqual(client.calls[0][1]["track_name"], "Never Gonna Give You Up")
        again = lyrics.fetch(self.lib, row["id"], client_factory=lambda: FakeClient([]))
        self.assertTrue(again["cached"])
        self.assertEqual(again["plain"], "a\nb\nc\nd")


class PlayerTest(unittest.TestCase):
    def test_state_commands_wait(self):
        from app.music.player import PlayerHub
        with tempfile.TemporaryDirectory() as tmp:
            hub = PlayerHub(Path(tmp) / "player.json")
            v0 = hub.version
            got = {}
            thread = threading.Thread(target=lambda: got.update(hub.wait(v0, 0, 5)))
            thread.start()
            time.sleep(0.1)
            hub.command("pause", {"target": "pc"}, "phone")
            thread.join(3)
            self.assertTrue(got["changed"])
            self.assertEqual(got["commands"][0]["cmd"], "pause")
            hub.update({"track_id": "abc", "position": 10, "playing": True, "queue": ["abc", 1], "duration": 100})
            time.sleep(0.05)
            snap = hub.snapshot()
            self.assertGreater(snap["position_now"], 10)
            self.assertEqual(snap["queue"], ["abc", "1"])
            with self.assertRaises(ValueError):
                hub.command("explode", {})
            again = PlayerHub(Path(tmp) / "player.json")
            self.assertEqual((again.state["track_id"], again.state["playing"]), ("abc", False))
            self.assertFalse(hub.wait(hub.version, -1, 0.05)["changed"])


class ToolsTest(Base):
    def test_update_downloads_release(self):
        from app.music import tools

        class Stream:
            def __init__(self):
                self.status_code = 200

            def raise_for_status(self):
                pass

            def iter_bytes(self, n):
                yield b"#!/bin/sh\necho 2099.01.01\n" + b"#" * 1_100_000

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class Client:
            def stream(self, method, url, headers=None):
                self.url = url
                return Stream()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        result = tools.update_ytdlp(client_factory=Client)
        self.assertTrue(result["ok"], result)
        self.assertTrue(tools.own_binary().is_file())
        self.assertEqual(tools.ytdlp_binary(), str(tools.own_binary()))
        self.assertIn(tools.release_asset(), ("yt-dlp.exe", "yt-dlp_x86.exe", "yt-dlp_macos", "yt-dlp_linux", "yt-dlp_linux_aarch64"))
        with patch("sys.platform", "win32"):
            self.assertIn("creationflags", tools.popen_kwargs())


class RoutesTest(Base):
    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient
        from app import pgdata
        (self.data / "pg").mkdir()
        self._p = [patch.object(pgdata, "MEMU_DIR", self.data / "pg"),
                   patch("app.modules_host.default_root", return_value=self.data / "modules")]
        for p in self._p:
            p.start()
        from app.playground_api import create_app
        self.client = TestClient(create_app(), base_url="http://127.0.0.1")

    def tearDown(self):
        for p in reversed(self._p):
            p.stop()
        super().tearDown()

    def test_call_stream_cover_and_boundary(self):
        c = self.client
        row = self.lib.add({"file": str(self.make_file("s.m4a", 10000)), "title": "S", "artist": "A"}, png_bytes())
        r = c.post("/api/pg/music/call", json={"op": "library.get", "args": {"id": row["id"]}})
        self.assertEqual(r.status_code, 200, r.text)
        track = r.json()["result"]
        self.assertTrue(track["stream"].startswith("http://127.0.0.1/api/pg/music/stream/"))
        stream = track["stream"].replace("http://127.0.0.1", "")
        whole = c.get(stream)
        self.assertEqual((whole.status_code, whole.headers["accept-ranges"], len(whole.content)), (200, "bytes", 10000))
        self.assertEqual(whole.headers["content-type"], "audio/mp4")
        part = c.get(stream, headers={"Range": "bytes=100-199"})
        self.assertEqual((part.status_code, part.headers["content-range"], part.content), (206, "bytes 100-199/10000",
                                                                                         whole.content[100:200]))
        tail = c.get(stream, headers={"Range": "bytes=-10"})
        self.assertEqual(tail.content, whole.content[-10:])
        self.assertEqual(c.get(stream, headers={"Range": "bytes=20000-"}).status_code, 416)
        self.assertEqual(c.get(stream.split("?")[0] + "?t=bad").status_code, 403)
        # The sandboxed module iframe is cross-site: media GETs pass, the JSON API does not.
        cross = {"Sec-Fetch-Site": "cross-site"}
        self.assertEqual(c.get(stream, headers=cross).status_code, 200)
        self.assertEqual(c.get(track["cover"].replace("http://127.0.0.1", ""), headers=cross).headers["content-type"], "image/jpeg")
        self.assertEqual(c.post("/api/pg/music/call", json={"op": "library.stats"}, headers=cross).status_code, 403)
        self.assertEqual(c.post("/api/pg/music/call", json={"op": "nope"}).status_code, 400)
        self.assertEqual(c.post("/api/pg/music/call", json={"op": "library.get", "args": {"id": "x"}}).status_code, 404)
        # A module without the «music» permission is refused.
        r = c.post("/api/pg/music/call", json={"op": "library.stats", "module_id": "translator"})
        self.assertEqual(r.status_code, 403)
        r = c.post("/api/pg/music/call", json={"op": "playlists.list", "module_id": "music", "lang": "en"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["result"][0]["name"], "Recently added")

    def test_module_document_csp(self):
        from app import modules_host
        modules_host.configure_module(self.data / "modules", "music", enabled=True)
        c = self.client
        doc = c.get("/api/pg/modules/music/document")
        self.assertEqual(doc.status_code, 200, doc.text[:200])
        self.assertIn("media-src data: blob: http://127.0.0.1:*/api/pg/music/", doc.headers["content-security-policy"])
        self.assertIn("media-src data: blob: http://127.0.0.1:*/api/pg/music/", doc.text)
        self.assertIn("musicApi.call", doc.text)
        other = c.get("/api/pg/modules/translator/document")
        self.assertNotIn("/api/pg/music/", other.headers["content-security-policy"])


class BridgeTablesTest(unittest.TestCase):
    def test_permission_tables(self):
        from app.modules_host import ACTION_PERMISSION, IMPLICIT_PERMISSIONS, PERMISSIONS
        self.assertEqual(ACTION_PERMISSION["music_call"], "music")
        self.assertIn("music", PERMISSIONS)
        self.assertNotIn("music", IMPLICIT_PERMISSIONS)
        js = (ROOT / "app" / "pgstatic" / "modules.js").read_text(encoding="utf-8")
        self.assertIn('music_call: "music"', js)
        self.assertIn('case "music_call":', js)
        from app.music import api
        self.assertTrue(api.is_public_media_path("/api/pg/music/stream/abcdef012345"))
        self.assertFalse(api.is_public_media_path("/api/pg/music/call"))
        self.assertFalse(api.is_public_media_path("/api/pg/music/stream/../x"))


if __name__ == "__main__":
    unittest.main()
