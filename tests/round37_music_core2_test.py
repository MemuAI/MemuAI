# -*- coding: utf-8 -*-
"""Round 37, second wave of the MemuMusic backend (app/music/): CORS on media, player state, job persistence,
parallel downloads, tag embedding, background lyrics, listening history aggregates, YouTube fallbacks. No network."""
from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from round37_music_core_test import Base, FakeClient, FakeResponse, FakeRunner, png_bytes  # noqa: E402

_PYDEPS = Path("/workspace/work/pydeps")

def _mutagen():
    try:
        import mutagen  # noqa: F401
        return True
    except ImportError:
        if _PYDEPS.is_dir() and str(_PYDEPS) not in sys.path:
            sys.path.append(str(_PYDEPS))
        try:
            import mutagen  # noqa: F401,F811
            return True
        except ImportError:
            return False

def wait_for(predicate, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()

class CorsTest(Base):
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

    def test_media_cors_from_sandboxed_module(self):
        c = self.client
        row = self.lib.add({"file": str(self.make_file("s.m4a", 4000)), "title": "S", "artist": "A"}, png_bytes())
        track = c.post("/api/pg/music/call", json={"op": "library.get", "args": {"id": row["id"]}}).json()["result"]
        stream = track["stream"].replace("http://127.0.0.1", "")
        cover = track["cover"].replace("http://127.0.0.1", "")
        sandbox = {"Origin": "null", "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "cors"}
        r = c.get(stream, headers={**sandbox, "Range": "bytes=0-99"})
        self.assertEqual(r.status_code, 206, r.text[:200])
        self.assertEqual(r.headers["access-control-allow-origin"], "*")
        expose = r.headers["access-control-expose-headers"]
        for name in ("Content-Range", "Accept-Ranges", "Content-Length"):
            self.assertIn(name, expose)
        r = c.get(cover, headers=sandbox)
        self.assertEqual((r.status_code, r.headers["access-control-allow-origin"]), (200, "*"))

        r = c.options(stream, headers={**sandbox, "Access-Control-Request-Method": "GET",
                                       "Access-Control-Request-Headers": "range"})
        self.assertEqual(r.status_code, 204)
        self.assertEqual(r.headers["access-control-allow-origin"], "*")
        self.assertIn("Range", r.headers["access-control-allow-headers"])
        self.assertIn("GET", r.headers["access-control-allow-methods"])

        self.assertEqual(c.head(stream, headers=sandbox).status_code, 200)

        bad = c.get(stream.split("?")[0] + "?t=bad", headers=sandbox)
        self.assertEqual((bad.status_code, bad.headers.get("access-control-allow-origin")), (403, "*"))

        self.assertEqual(c.post("/api/pg/music/call", json={"op": "library.stats"}, headers={"Origin": "null"}).status_code, 403)
        self.assertEqual(c.options("/api/pg/music/call", headers=sandbox).status_code, 403)
        r = c.get(stream, headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status_code, 200)

class PlayerStateTest(unittest.TestCase):
    def test_playing_flag_and_position(self):
        import tempfile
        from app.music import player
        with tempfile.TemporaryDirectory() as tmp:
            hub = player.PlayerHub(Path(tmp) / "player.json")
            hub.update({"track_id": "aaaaaaaaaaaa", "position": 5, "playing": True, "duration": 300, "device": "pc"})
            self.assertTrue(hub.snapshot()["playing"])
            self.assertTrue(hub.snapshot()["online"])

            self.assertFalse(hub.update({"playing": "false"})["playing"])
            self.assertTrue(hub.update({"playing": "true"})["playing"])

            t0 = time.time()
            with patch("app.music.player.time.time", return_value=t0 + 1):
                hub.update({"track_id": "aaaaaaaaaaaa", "position": 50, "playing": True})
            with patch("app.music.player.time.time", return_value=t0 + 3):
                snap = hub.update({"playing": False})
                self.assertAlmostEqual(snap["position_now"], 52, delta=0.01)
            with patch("app.music.player.time.time", return_value=t0 + 100):
                snap = hub.update({"playing": True})
                self.assertAlmostEqual(snap["position_now"], 52, delta=0.01)
            with patch("app.music.player.time.time", return_value=t0 + 104):
                self.assertAlmostEqual(hub.snapshot()["position_now"], 56, delta=0.01)

            hub.update({"playing": True, "position": 10, "device": "pc", "at": 2_000_000_000_000})
            snap = hub.update({"playing": False, "position": 9, "device": "pc", "at": 1_999_999_999_000})
            self.assertTrue(snap["ignored"])
            self.assertTrue(hub.snapshot()["playing"])

            with patch("app.music.player.time.time", return_value=time.time() + player.STALE_SECONDS + 5):
                snap = hub.snapshot()
                self.assertFalse(snap["playing"])
                self.assertTrue(snap["stale"])
                self.assertFalse(snap["online"])

class SlowRunner(FakeRunner):
    """Counts how many downloads run at the same time."""
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.now = 0
        self.peak = 0
        self.lock = threading.Lock()

    def download(self, *a, **kw):
        with self.lock:
            self.now += 1
            self.peak = max(self.peak, self.now)
        try:
            return super().download(*a, **kw)
        finally:
            with self.lock:
                self.now -= 1

class JobsTest(Base):
    def manager(self, runner, routes=None, **kw):
        from app.music.downloader import Manager
        client = FakeClient(routes or [("https://i.ytimg.com/", FakeResponse(200, content=png_bytes(), text="img"))])
        return Manager(library_factory=lambda: self.lib, runner_factory=lambda: runner, client_factory=lambda: client, **kw)

    def playlist(self, n, prefix="v"):
        url = "https://www.youtube.com/playlist?list=PLx"
        entries = [{"url": f"https://www.youtube.com/watch?v={prefix}{i:010d}", "title": f"T{i}"} for i in range(n)]
        return url, {url: {"title": "Mix", "entries": entries}}

    def test_parallel_items_order_and_setting(self):
        url, flat = self.playlist(6)
        runner = SlowRunner(self.lib.tracks_dir, flat=flat, delay=0.03)
        mgr = self.manager(runner)
        self.assertEqual(mgr.parallel(), 2)
        job_id = mgr.start(url)["job_id"]
        self.assertTrue(wait_for(lambda: mgr.jobs[job_id].status == "done", 10), mgr.jobs[job_id].public())
        self.assertEqual(runner.peak, 2)
        public = mgr.list()[0]
        self.assertEqual((public["total"], public["done"], public["failed"]), (6, 6, 0))
        self.assertEqual([i["status"] for i in public["items"]], ["done"] * 6)
        self.assertTrue(all(i["progress"] == 1 for i in public["items"]))
        self.assertEqual(public["items"][0]["title"], "T0")

        titles = [self.lib.row(t)["source_url"][-11:] for t in public["track_ids"]]
        self.assertEqual(titles, [f"v{i:010d}" for i in range(6)])

        self.lib.set_settings({"parallel_downloads": 1})
        with self.assertRaises(ValueError):
            self.lib.set_settings({"parallel_downloads": "many"})
        self.assertEqual(self.lib.set_settings({"parallel_downloads": 9})["parallel_downloads"], 4)
        self.lib.set_settings({"parallel_downloads": 1})
        url2, flat2 = self.playlist(3, "w")
        runner2 = SlowRunner(self.lib.tracks_dir, flat=flat2, delay=0.02)
        mgr2 = self.manager(runner2)
        jid = mgr2.start(url2)["job_id"]
        self.assertTrue(wait_for(lambda: mgr2.jobs[jid].status == "done", 10))
        self.assertEqual(runner2.peak, 1)

    def test_two_jobs_share_the_slots(self):
        runner = SlowRunner(self.lib.tracks_dir, delay=0.05)
        mgr = self.manager(runner)
        ids = [mgr.start(f"https://www.youtube.com/watch?v=job{i:08d}")["job_id"] for i in range(3)]
        self.assertTrue(wait_for(lambda: all(mgr.jobs[j].status == "done" for j in ids), 10))
        self.assertEqual(runner.peak, 2)

    def test_retry_only_failed_and_persistence(self):
        url, flat = self.playlist(3)
        bad = "https://www.youtube.com/watch?v=v0000000001"
        runner = FakeRunner(self.lib.tracks_dir, flat=flat, fail_urls={bad})
        mgr = self.manager(runner)
        job_id = mgr.start(url)["job_id"]
        self.assertTrue(wait_for(lambda: mgr.jobs[job_id].status == "done", 10))
        public = mgr.list()[0]
        self.assertEqual([i["status"] for i in public["items"]], ["done", "error", "done"])
        self.assertEqual(public["items"][1]["error_code"], "youtube_bot")
        self.assertEqual(public["error_code"], "youtube_bot")
        self.assertIn("cookies", public["items"][1]["error"].lower())
        self.assertIn("yt-dlp", public["items"][1]["error"])

        saved = self.lib.root / "download-jobs.json"
        self.assertTrue(saved.is_file())
        mgr2 = self.manager(runner)
        again = mgr2.list()
        self.assertEqual(again[0]["job_id"], job_id)
        self.assertEqual([i["status"] for i in again[0]["items"]], ["done", "error", "done"])
        runner.fail_urls.clear()
        before = len(runner.downloads)
        self.assertEqual(mgr2.retry(job_id)["retried"], 1)
        self.assertTrue(wait_for(lambda: mgr2.jobs[job_id].status == "done", 10))
        self.assertEqual(len(runner.downloads) - before, 1)
        self.assertEqual(runner.downloads[-1][0], bad)
        self.assertEqual([i["status"] for i in mgr2.list()[0]["items"]], ["done"] * 3)
        self.assertEqual(len(mgr2.list()[0]["track_ids"]), 3)

    def test_interrupted_job_after_restart(self):
        import json
        from app.music.downloader import Job
        job = Job("https://www.youtube.com/playlist?list=PLz", "download", "", {"source": "youtube", "kind": "playlist"})
        job.status = "running"
        kept = self.lib.add({"file": str(self.make_file("kept.m4a")), "title": "a", "artist": "x"})
        job.items = [{"title": "a", "url": "https://www.youtube.com/watch?v=a0000000000", "status": "done",
                      "track_id": kept["id"]},
                     {"title": "b", "url": "https://www.youtube.com/watch?v=b0000000000", "status": "running"},
                     {"title": "c", "url": "https://www.youtube.com/watch?v=c0000000000", "status": "pending"}]
        (self.lib.root / "download-jobs.json").write_text(json.dumps({"jobs": [job.to_dict()]}), encoding="utf-8")
        runner = FakeRunner(self.lib.tracks_dir)
        mgr = self.manager(runner)
        public = mgr.list()[0]
        self.assertEqual((public["status"], public["error_code"], public["interrupted"]), ("error", "interrupted", True))
        self.assertEqual([i["status"] for i in public["items"]], ["done", "pending", "pending"])
        self.assertEqual(len(runner.downloads), 0)
        self.assertEqual(mgr.retry(job.job_id)["retried"], 2)
        self.assertTrue(wait_for(lambda: mgr.jobs[job.job_id].status == "done", 10))
        self.assertEqual(len(runner.downloads), 2)

        self.lib.delete(kept["id"])
        mgr.jobs[job.job_id].items[1]["status"] = "error"
        self.assertEqual(mgr.retry(job.job_id)["retried"], 2)
        self.assertTrue(wait_for(lambda: mgr.jobs[job.job_id].status == "done", 10))
        self.assertEqual(len(runner.downloads), 3)

        (self.lib.root / "download-jobs.json").write_text("{oops", encoding="utf-8")
        self.assertEqual(self.manager(runner).list(), [])

    def test_cancel_parallel_items(self):
        url, flat = self.playlist(5)
        runner = SlowRunner(self.lib.tracks_dir, flat=flat, delay=0.2)
        mgr = self.manager(runner)
        job_id = mgr.start(url)["job_id"]
        self.assertTrue(wait_for(lambda: runner.now >= 1, 5))
        mgr.cancel(job_id)
        self.assertTrue(wait_for(lambda: mgr.jobs[job_id].status == "cancelled", 10), mgr.jobs[job_id].status)
        self.assertTrue(wait_for(lambda: runner.now == 0, 5))
        self.assertEqual(mgr.active, 0)
        statuses = [i["status"] for i in mgr.list()[0]["items"]]
        self.assertNotIn("running", statuses)
        self.assertIn("cancelled", statuses)

class FallbackTest(Base):
    def test_blocked_youtube_link_falls_back_to_soundcloud(self):
        from app.music.downloader import Manager
        yt = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        runner = FakeRunner(self.lib.tracks_dir, fail_urls={yt, "https://music.youtube.com/watch?v=matchedID01"})
        oembed = FakeResponse(200, {"title": "Band - Song (Official Video)", "author_name": "BandVEVO"})
        client = FakeClient([("https://www.youtube.com/oembed", oembed),
                             ("https://i.ytimg.com/", FakeResponse(200, content=png_bytes(), text="img"))])
        mgr = Manager(library_factory=lambda: self.lib, runner_factory=lambda: runner, client_factory=lambda: client)
        job_id = mgr.start(yt)["job_id"]
        self.assertTrue(wait_for(lambda: mgr.jobs[job_id].status in ("done", "error"), 10))
        job = mgr.jobs[job_id]
        self.assertEqual(job.status, "done", job.public())

        self.assertEqual([u for u, _ in runner.downloads], [yt, "https://soundcloud.com/x/song"])
        self.assertEqual(job.items[0]["via"], "soundcloud")
        self.assertEqual(job.public()["items"][0]["via"], "soundcloud")

        again = mgr.start("https://youtu.be/dQw4w9WgXcQ")["job_id"]
        self.assertTrue(wait_for(lambda: mgr.jobs[again].status == "done", 10))
        self.assertEqual(mgr.jobs[again].items[0]["status"], "exists")

        self.lib.set_settings({"youtube_fallback": False})
        runner2 = FakeRunner(self.lib.tracks_dir, fail_urls={"https://www.youtube.com/watch?v=zzzzzzzzzzz"})
        mgr2 = Manager(library_factory=lambda: self.lib, runner_factory=lambda: runner2, client_factory=lambda: client)
        jid = mgr2.start("https://www.youtube.com/watch?v=zzzzzzzzzzz")["job_id"]
        self.assertTrue(wait_for(lambda: mgr2.jobs[jid].status == "error", 10))
        public = mgr2.list()[0]
        self.assertEqual(public["error_code"], "youtube_bot")
        self.assertIn("не бот", public["error"])
        self.assertEqual(len(runner2.downloads), 1)

    def test_spotify_chain_order(self):
        """Spotify → YouTube Music → YouTube → SoundCloud."""
        from app.music.downloader import Manager

        class ChainRunner(FakeRunner):
            def json(self, target, flat=True, limit=0, timeout=90):
                if target.startswith("ytsearch"):
                    return {"entries": [{"id": "ytBest00001", "title": "Band - Song", "duration": 200}]}
                return super().json(target, flat, limit, timeout)

        runner = ChainRunner(self.lib.tracks_dir, fail_urls={"https://music.youtube.com/watch?v=matchedID01"})
        runner.fail_urls = set()
        original = runner.download

        def download(url, *a, **kw):
            if "music.youtube.com" in url:
                runner.downloads.append((url, ""))
                raise RuntimeError("ERROR: HTTP Error 403: Forbidden")
            return original(url, *a, **kw)

        runner.download = download
        meta = {"kind": "track", "title": "Song", "artist": "Band", "items": [
            {"title": "Song", "artist": "Band", "duration": 200, "source_url": "https://open.spotify.com/track/abc"}]}
        client = FakeClient([("https://i.ytimg.com/", FakeResponse(200, content=png_bytes(), text="img"))])
        mgr = Manager(library_factory=lambda: self.lib, runner_factory=lambda: runner, client_factory=lambda: client)
        with patch("app.music.sources.resolve_meta", return_value=meta):
            job_id = mgr.start("https://open.spotify.com/track/abc")["job_id"]
            self.assertTrue(wait_for(lambda: mgr.jobs[job_id].status in ("done", "error"), 10))
        self.assertEqual([u for u, _ in runner.downloads], ["https://music.youtube.com/watch?v=matchedID01",
                                                             "https://www.youtube.com/watch?v=ytBest00001"])
        self.assertEqual(mgr.jobs[job_id].items[0]["via"], "youtube")
        self.assertEqual(self.lib.get(mgr.jobs[job_id].track_ids[0])["source"], "spotify")

class TaggerTest(Base):
    def _make_audio(self, ext):
        import shutil
        import subprocess
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.skipTest("no ffmpeg to make a sample file")
        path = self.lib.tracks_dir / f"tone{ext}"
        codec = {".m4a": ["-c:a", "aac"], ".mp3": ["-c:a", "libmp3lame"], ".opus": ["-c:a", "libopus"],
                 ".flac": ["-c:a", "flac"]}[ext]
        out = subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                              *codec, str(path)], capture_output=True)
        if out.returncode != 0:
            self.skipTest(f"ffmpeg cannot encode {ext}")
        return path

    def test_embed_tags_and_cover(self):
        if not _mutagen():
            self.skipTest("mutagen not installed")
        from mutagen import File as MutagenFile
        from app.music import tagger
        fields = {"title": "Песня", "artist": "Band", "album": "LP", "year": "2024", "track_no": 3,
                  "source_url": "https://open.spotify.com/track/abc"}
        cover = png_bytes((10, 120, 200), (300, 300))
        for ext in (".m4a", ".mp3", ".opus", ".flac"):
            path = self._make_audio(ext)
            result = tagger.embed(path, fields, cover)
            self.assertTrue(result["ok"], (ext, result))
            self.assertEqual(result["method"], "mutagen")
            easy = MutagenFile(str(path), easy=True)
            self.assertEqual(easy.get("title"), ["Песня"], ext)
            self.assertEqual(easy.get("artist"), ["Band"], ext)
            raw = MutagenFile(str(path))
            keys = " ".join(str(k) for k in (raw.tags.keys() if raw.tags else []))
            has_cover = ("covr" in keys or "APIC" in keys or "metadata_block_picture" in keys
                         or bool(getattr(raw, "pictures", [])))
            self.assertTrue(has_cover, (ext, keys))

    def test_without_mutagen_or_garbage_never_raises(self):
        from app.music import tagger
        path = self.make_file("junk.m4a", 3000)
        with patch.object(tagger, "available", return_value={"mutagen": False, "ffmpeg": False}), \
                patch("app.music.tools.ffmpeg_path", return_value=None):
            self.assertFalse(tagger.embed(path, {"title": "x"}, None)["ok"])
        result = tagger.embed(path, {"title": "x"}, b"not an image")
        self.assertIn("ok", result)
        self.assertEqual(path.stat().st_size >= 3000, True)
        self.assertFalse(tagger.embed(self.lib.tracks_dir / "missing.mp3", {"title": "x"})["ok"])

    def test_download_writes_tags(self):
        if not _mutagen():
            self.skipTest("mutagen not installed")
        from mutagen import File as MutagenFile
        from app.music.downloader import Manager
        sample = self._make_audio(".m4a")

        class RealFileRunner(FakeRunner):
            def download(self, url, fmt, outdir, embed, on_progress, cancel, holder, cookies_browser=""):
                info = super().download(url, fmt, outdir, embed, on_progress, cancel, holder, cookies_browser)
                Path(info["filepath"]).write_bytes(sample.read_bytes())
                return info

        runner = RealFileRunner(self.lib.tracks_dir)
        client = FakeClient([("https://i.ytimg.com/", FakeResponse(200, content=png_bytes(), text="img"))])
        mgr = Manager(library_factory=lambda: self.lib, runner_factory=lambda: runner, client_factory=lambda: client)
        jid = mgr.start("https://www.youtube.com/watch?v=dQw4w9WgXcQ")["job_id"]
        self.assertTrue(wait_for(lambda: mgr.jobs[jid].status == "done", 10), mgr.jobs[jid].public())
        row = self.lib.row(mgr.jobs[jid].track_ids[0])
        audio = MutagenFile(str(self.lib.file_path(row)), easy=True)
        self.assertEqual(audio.get("title"), ["Never Gonna Give You Up"])
        self.assertEqual(audio.get("artist"), ["Rick Astley"])
        self.assertTrue(MutagenFile(str(self.lib.file_path(row))).tags.get("covr"))
        self.assertEqual(row["size"], self.lib.file_path(row).stat().st_size)

class LyricsPrefetchTest(Base):
    def test_one_by_one_and_skips_cached(self):
        from app.music.lyrics import Prefetcher
        a = self.lib.add({"file": str(self.make_file("a.m4a")), "title": "Song A", "artist": "Band", "duration": 200})
        b = self.lib.add({"file": str(self.make_file("b.m4a")), "title": "Song B", "artist": "Band", "duration": 200})
        self.lib.store_lyrics(b["id"], None, "cached text", "lrclib")
        hit = {"syncedLyrics": "[00:01.00] one\n[00:02.00] two\n[00:03.00] three\n[00:04.00] four", "plainLyrics": None,
               "duration": 200}
        self.lib.set_settings({"lyrics_sources": ["lrclib"]})
        client = FakeClient([("https://lrclib.net/api/get", FakeResponse(200, hit))])
        pre = Prefetcher(library_factory=lambda: self.lib, client_factory=lambda: client, pause=0.01)
        self.assertTrue(pre.add(a["id"]))
        self.assertFalse(pre.add(a["id"]) and pre.status()["queued"] > 1)
        pre.add(b["id"])
        self.assertTrue(wait_for(lambda: not pre.status()["running"], 5))
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(self.lib.cached_lyrics(a["id"])["synced"][0], [1.0, "one"])
        self.assertEqual(pre.status()["done"], 1)
        self.lib.set_settings({"auto_lyrics": False})
        c = self.lib.add({"file": str(self.make_file("c.m4a")), "title": "C", "artist": "Band"})
        self.assertFalse(pre.add(c["id"]))

    def test_manager_hook(self):
        from app.music.downloader import Manager
        added = []
        runner = FakeRunner(self.lib.tracks_dir)
        client = FakeClient([("https://i.ytimg.com/", FakeResponse(200, content=png_bytes(), text="img"))])
        mgr = Manager(library_factory=lambda: self.lib, runner_factory=lambda: runner, client_factory=lambda: client,
                      on_track_added=added.append)
        jid = mgr.start("https://www.youtube.com/watch?v=dQw4w9WgXcQ")["job_id"]
        self.assertTrue(wait_for(lambda: mgr.jobs[jid].status == "done", 10))
        self.assertEqual(added, mgr.jobs[jid].track_ids)

class HistoryStatsTest(Base):
    def test_history_since_and_aggregates(self):
        from app.music import api
        a = self.lib.add({"file": str(self.make_file("a.m4a")), "title": "A", "artist": "X", "duration": 180})
        now = time.time()
        api.call("library.played", {"id": a["id"], "seconds": 180, "device": "phone", "at": now - 2 * 86400})
        api.call("library.played", {"id": a["id"], "seconds": 90, "at": (now - 3600) * 1000})
        api.call("library.played", {"id": a["id"], "seconds": 10})
        api.call("library.played", {"id": a["id"], "seconds": 60, "at": now + 10 * 86400})
        hist = api.call("library.history", {"limit": 10})
        self.assertEqual(len(hist), 4)
        self.assertEqual(hist[-1]["device"], "phone")
        self.assertEqual(hist[0]["device"], "pc")
        recent = api.call("library.history", {"since": now - 7200})
        self.assertEqual(len(recent), 3)
        older = api.call("library.history", {"until": (now - 7200) * 1000})
        self.assertEqual([h["seconds"] for h in older], [180])
        with self.assertRaises(ValueError):
            api.call("library.history", {"since": "yesterday"})
        stats = api.call("library.stats", {"days": 7})
        self.assertEqual(stats["days"], 7)
        self.assertEqual(len(stats["by_day"]), 7)
        self.assertEqual(stats["by_day"][-1]["day"], time.strftime("%Y-%m-%d"))
        self.assertEqual(sum(d["listens"] for d in stats["by_day"]), 4)
        self.assertEqual(sum(d["plays"] for d in stats["by_day"]), 3)
        self.assertAlmostEqual(sum(d["minutes"] for d in stats["by_day"]), 340 / 60, delta=0.2)
        self.assertEqual(len(stats["by_hour"]), 24)
        self.assertEqual(sum(h["listens"] for h in stats["by_hour"]), 4)
        self.assertEqual(len(stats["by_weekday"]), 7)
        self.assertEqual({d["device"] for d in stats["by_device"]}, {"pc", "phone"})
        self.assertEqual(api.call("library.stats", {})["days"], 30)
        self.assertEqual(len(api.call("library.stats", {})["by_day"]), 30)
        self.assertEqual(self.lib.row(a["id"])["plays"], 3)

if __name__ == "__main__":
    unittest.main()
