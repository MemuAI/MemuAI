# -*- coding: utf-8 -*-
"""Round 38, MemuMusic features (app/music/extras.py + library/api changes, module front feat-*.js). No network."""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from round37_music_core_test import Base, FakeClient, FakeResponse, png_bytes  # noqa: E402

MODULE = ROOT / "app" / "module_builtins" / "music"

def call(op, args=None):
    from app.music import api
    return api.call(op, args or {})

class SmartPlaylistsTest(Base):
    def add(self, name, **fields):
        return self.lib.add({"file": str(self.make_file(name + ".m4a", 3000)), "title": name, **fields})

    def test_recent_counts_and_covers_follow_changes(self):
        """The bug of r37: «Recently added» said 0 tracks while it had one — counts must be live on every list."""
        lists = {p["id"]: p for p in call("playlists.list")}
        self.assertEqual(lists["smart:recent"]["count"], 0)
        row = self.lib.add({"file": str(self.make_file("one.m4a", 3000)), "title": "One", "duration": 61}, png_bytes())
        lists = {p["id"]: p for p in call("playlists.list")}
        self.assertEqual(lists["smart:recent"]["count"], 1)
        self.assertEqual(lists["smart:recent"]["duration"], 61)
        self.assertTrue(lists["smart:recent"]["cover"])
        self.assertEqual(lists["smart:recent"]["track_ids"], [row["id"]])
        call("library.like", {"id": row["id"], "liked": True})
        call("library.played", {"id": row["id"], "seconds": 60})
        lists = {p["id"]: p for p in call("playlists.list")}
        self.assertEqual((lists["smart:liked"]["count"], lists["smart:top"]["count"]), (1, 1))
        self.assertEqual(call("playlist.get", {"id": "smart:recent"})["count"], 1)

    def test_hide_and_order_smart_playlists(self):
        call("settings.set", {"smart_hidden": ["smart:top", "smart:liked"], "smart_order": ["smart:liked", "smart:recent"]})
        ids = [p["id"] for p in call("playlists.list")]
        self.assertEqual(ids, ["smart:recent"])
        every = call("playlists.list", {"all": True})
        self.assertEqual([p["id"] for p in every][:3], ["smart:liked", "smart:recent", "smart:top"])
        self.assertEqual({p["id"]: p["hidden"] for p in every}, {"smart:liked": True, "smart:recent": False, "smart:top": True})
        call("settings.set", {"smart_hidden": []})
        self.assertEqual(len(call("playlists.list")), 3)

    def test_recent_days_and_limit(self):
        old = self.add("Old")
        self.lib._x("UPDATE tracks SET added_at=? WHERE id=?", (time.time() - 90 * 86400, old["id"]))
        self.add("New")
        call("settings.set", {"recent_days": 30})
        self.assertEqual([t["title"] for t in call("playlist.get", {"id": "smart:recent"})["tracks"]], ["New"])
        call("settings.set", {"recent_days": 0})
        self.assertEqual(call("playlist.get", {"id": "smart:recent"})["count"], 2)

    def test_rule_playlist(self):
        a = self.add("Alpha", artist="Daft Punk", genre="Electronic; House", year="2013", duration=320)
        self.add("Beta", artist="Daft Punk", genre="Electronic", year="2001", duration=200)
        self.add("Gamma", artist="Queen", genre="Rock", year="1975", duration=355)
        call("library.like", {"id": a["id"], "liked": True})
        rules = {"match": "all", "conditions": [{"field": "artist", "op": "contains", "value": "daft"},
                                                {"field": "duration", "op": "gt", "value": 250}], "sort": "title"}
        self.assertEqual(call("playlist.preview", {"rules": rules})["count"], 1)
        pl = call("playlist.create", {"name": "Long Daft", "rules": rules})
        self.assertEqual([t["title"] for t in pl["tracks"]], ["Alpha"])
        self.assertEqual(pl["rules"]["conditions"][1]["value"], 250.0)
        lists = {p["id"]: p for p in call("playlists.list")}
        self.assertTrue(lists[pl["id"]]["rules"])
        self.assertEqual(lists[pl["id"]]["count"], 1)
        any_rules = {"match": "any", "conditions": [{"field": "genre", "op": "contains", "value": "rock"},
                                                    {"field": "liked", "op": "is", "value": True}], "sort": "title"}
        pl = call("playlist.update", {"id": pl["id"], "rules": any_rules})
        self.assertEqual([t["title"] for t in pl["tracks"]], ["Alpha", "Gamma"])
        days = {"conditions": [{"field": "added_days", "op": "within", "value": 7}, {"field": "year", "op": "between", "value": "2000-2010"}]}
        self.assertEqual([t["title"] for t in call("playlist.preview", {"rules": days})["tracks"]], ["Beta"])
        with self.assertRaises(ValueError):
            call("playlist.preview", {"rules": {"conditions": [{"field": "file; DROP TABLE tracks", "op": "is", "value": 1}]}})
        with self.assertRaises(ValueError):
            call("playlist.preview", {"rules": {"conditions": [{"field": "plays", "op": "contains", "value": 1}]}})

    def test_playlist_own_cover(self):
        pl = call("playlist.create", {"name": "Mine"})
        out = call("playlist.cover", {"id": pl["id"], "data": base64.b64encode(png_bytes(size=(300, 200))).decode()})
        self.assertTrue(out["cover"].startswith("data:image/jpeg;base64,"))
        self.assertTrue(out["own_cover"])
        listed = next(p for p in call("playlists.list") if p["id"] == pl["id"])
        self.assertTrue(listed["cover"].startswith("data:image/jpeg"))
        out = call("playlist.cover", {"id": pl["id"], "clear": True})
        self.assertIsNone(out["cover"])
        with self.assertRaises(ValueError):
            call("playlist.cover", {"id": "smart:liked", "clear": True})

class TrackEditTest(Base):
    def test_cover_from_data_url_and_clear(self):
        row = self.lib.add({"file": str(self.make_file("x.m4a")), "title": "X"})
        data = "data:image/png;base64," + base64.b64encode(png_bytes((10, 200, 30), (400, 400))).decode()
        t = call("track.cover", {"id": row["id"], "data": data, "write_tags": False})
        self.assertTrue(t["cover"])
        self.assertTrue(t["accent"].startswith("#"))
        t = call("track.cover", {"id": row["id"], "clear": True})
        self.assertIsNone(t["cover"])
        with self.assertRaises(ValueError):
            call("track.cover", {"id": row["id"], "data": base64.b64encode(b"not an image at all" * 10).decode(), "write_tags": False})

    def test_cover_from_url_and_youtube_frame(self):
        from app.music import extras
        row = self.lib.add({"file": str(self.make_file("y.m4a")), "title": "Y", "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
        client = FakeClient([("https://img.example/", FakeResponse(content=png_bytes(size=(500, 500)))),
                             ("https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg", FakeResponse(content=png_bytes(size=(1280, 720))))])
        t = extras.set_cover(self.lib, row["id"], {"url": "https://img.example/a.png", "write_tags": False}, lambda: client)
        self.assertTrue(t["cover"])
        with patch("app.music.tools.ffmpeg_path", return_value=None):
            t = extras.set_cover(self.lib, row["id"], {"video": 12, "write_tags": False}, lambda: client)
        from PIL import Image
        with Image.open(self.lib.cover_path(row["id"])) as img:
            self.assertEqual(img.size[0], img.size[1])
        with self.assertRaises(ValueError):
            extras.set_cover(self.lib, row["id"], {"url": "file:///etc/passwd"}, lambda: client)

    def test_cover_search_itunes_and_deezer(self):
        from app.music import extras
        client = FakeClient([
            ("https://itunes.apple.com/search", FakeResponse(data={"results": [{"trackName": "Get Lucky", "artistName": "Daft Punk",
                "collectionName": "RAM", "artworkUrl100": "https://is1.mzstatic.com/a/100x100bb.jpg"}]})),
            ("https://api.deezer.com/search", FakeResponse(data={"data": [{"title": "Get Lucky", "artist": {"name": "Daft Punk"},
                "album": {"title": "RAM", "cover_xl": "https://e-cdns/xl.jpg", "cover_medium": "https://e-cdns/m.jpg"}}]})),
        ])
        found = extras.cover_search("daft punk get lucky", 5, lambda: client)
        self.assertEqual([f["source"] for f in found], ["itunes", "deezer"])
        self.assertIn("1200x1200bb", found[0]["url"])
        self.assertEqual(extras.cover_search("", 5, lambda: client), [])

    def test_bulk_edit_and_tags_written(self):
        a = self.lib.add({"file": str(self.make_file("a.m4a")), "title": "A", "artist": "x"})
        b = self.lib.add({"file": str(self.make_file("b.m4a")), "title": "B", "artist": "y"})
        with patch("app.music.tagger.embed", return_value={"ok": True}) as embed:
            out = call("library.update_many", {"ids": [a["id"], b["id"]], "changes": {"artist": "Same", "album": "LP", "genre": "Jazz", "title": None}})
            self.assertEqual(embed.call_count, 2)
        self.assertEqual({t["artist"] for t in out["tracks"]}, {"Same"})
        self.assertEqual([t["title"] for t in out["tracks"]], ["A", "B"])
        with patch("app.music.tagger.embed") as embed:
            call("library.update", {"id": a["id"], "title": "A2", "write_tags": False})
            embed.assert_not_called()
        with self.assertRaises(ValueError):
            call("library.update_many", {"ids": [], "changes": {"artist": "z"}})

class LibraryToolsTest(Base):
    def add(self, name, **fields):
        return self.lib.add({"file": str(self.make_file(name + ".m4a", 3000)), "title": name, **fields})

    def test_genres_filter_and_search(self):
        self.add("One", genre="Rock; Pop")
        self.add("Two", genre="pop")
        self.add("Three", genre="Jazz", year="1959")
        names = {g["name"].lower(): g["tracks"] for g in call("library.genres")}
        self.assertEqual(names, {"pop": 2, "rock": 1, "jazz": 1})
        self.assertEqual(call("library.list", {"filter": "genre:pop"})["total"], 2)
        self.assertEqual(call("library.list", {"q": "1959"})["tracks"][0]["title"], "Three")
        self.assertEqual(call("library.list", {"q": "jazz"})["total"], 1)

    def test_folders(self):
        outside = self.data / "ImportedMusic" / "Album"
        outside.mkdir(parents=True)
        f = outside / "s.m4a"
        f.write_bytes(b"x" * 4000)
        self.lib.add({"file": str(f), "title": "Ext"})
        self.add("Own")
        folders = {Path(x["path"]).name: x["tracks"] for x in call("library.folders")}
        self.assertEqual(folders, {"Album": 1, "tracks": 1})
        self.assertEqual(call("library.list", {"filter": "folder:" + str(self.data / "ImportedMusic")})["total"], 1)

    def test_duplicates_and_radio(self):
        self.add("Song", artist="A", duration=200, bitrate=128)
        self.add("Song (Official Video)", artist="A", duration=201, bitrate=256)
        self.add("Song", artist="A", duration=400)
        groups = call("library.duplicates")
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["tracks"][0]["bitrate"], 256)
        seed = self.add("Seed", artist="Band", genre="Rock")
        self.add("Same band", artist="Band")
        self.add("Same genre", artist="Other", genre="rock")
        radio = call("library.radio", {"id": seed["id"], "limit": 10})
        titles = [t["title"] for t in radio["tracks"]]
        self.assertEqual(titles[0], "Seed")
        self.assertEqual(set(titles[1:3]), {"Same band", "Same genre"})
        self.assertIn("Band", radio["online_query"])

    def test_m3u_roundtrip(self):
        a = self.add("A", artist="X", duration=100)
        b = self.add("B", artist="Y", duration=50)
        pl = call("playlist.create", {"name": "Trip / 1", "track_ids": [a["id"], b["id"]]})
        out = call("playlist.export", {"id": pl["id"], "path": str(self.data / "exp")})
        self.assertEqual(out["filename"], "Trip _ 1.m3u8")
        text = Path(out["path"]).read_text(encoding="utf-8")
        self.assertIn("#EXTINF:100,X - A", text)
        back = call("playlist.import", {"path": out["path"]})
        self.assertEqual(back["matched"], 2)
        self.assertEqual([t["id"] for t in back["playlist"]["tracks"]], [a["id"], b["id"]])
        by_name = call("playlist.import", {"text": "#EXTM3U\n#EXTINF:50,Y - B\nC:/gone/b.mp3\nnothing.mp3\n", "name": "N"})
        self.assertEqual((by_name["matched"], by_name["missing"]), (1, ["nothing.mp3"]))

    def test_upload_resume_reveal(self):
        data = base64.b64encode(b"\x00" * 3000).decode()
        with patch("app.music.extras.analyzer"):
            t = call("library.upload", {"name": "../evil/Artist - Dropped.opus", "data": data})
        self.assertEqual((t["artist"], t["title"]), ("Artist", "Dropped"))
        row = self.lib.row(t["id"])
        self.assertTrue(Path(row["file"]).is_absolute() is False or "Imported" in row["file"])
        self.assertTrue((self.lib.tracks_dir / "Imported").is_dir())
        with self.assertRaises(ValueError):
            call("library.upload", {"name": "x.exe", "data": data})
        call("library.resume", {"id": t["id"], "position": 125.5})
        self.assertEqual(call("library.get", {"id": t["id"]})["resume_at"], 125.5)
        from app.music import extras
        seen = []
        out = extras.reveal(self.lib, t["id"], opener=lambda p: seen.append(p) or True)
        self.assertTrue(out["opened"])
        self.assertTrue(seen[0].name.endswith("Dropped.opus"))

    def test_replace_track_keeps_history(self):
        from app.music import extras
        old = self.add("Old", artist="A", genre="Pop")
        new = self.add("new (Official)", artist="")
        call("library.like", {"id": old["id"], "liked": True})
        call("library.played", {"id": old["id"], "seconds": 100})
        pl = call("playlist.create", {"name": "P", "track_ids": [old["id"]]})
        extras.replace_track(self.lib, old["id"], new["id"])
        t = call("library.get", {"id": new["id"]})
        self.assertEqual((t["title"], t["liked"], t["plays"], t["genre"]), ("Old", True, 1, "Pop"))
        self.assertEqual([x["id"] for x in call("playlist.get", {"id": pl["id"]})["tracks"]], [new["id"]])
        self.assertIsNone(self.lib.row(old["id"]))

    def test_settings_validation(self):
        out = call("settings.set", {"crossfade": 99, "seek_step": "5", "play_click": "single", "gapless": "false",
                                    "hotkeys": {"next": "Ctrl+ArrowRight"}, "smart_hidden": "nope", "unknown": 1})
        self.assertEqual((out["crossfade"], out["seek_step"], out["play_click"], out["gapless"]), (12, 5, "single", False))
        self.assertEqual((out["hotkeys"], out["smart_hidden"]), ({"next": "Ctrl+ArrowRight"}, []))
        self.assertNotIn("unknown", out)
        with self.assertRaises(ValueError):
            call("settings.set", {"play_click": "triple"})

    def test_loudness_gain_follows_target(self):
        from app.music import extras
        t = self.add("Loud")
        self.assertEqual(extras.store_loudness(self.lib, t["id"], -8.0), -6.0)
        call("settings.set", {"normalize_target": -16})
        self.assertEqual(call("library.get", {"id": t["id"]})["gain"], -8.0)

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg missing")
    def test_measure_lufs_with_ffmpeg(self):
        from app.music import extras
        path = self.lib.tracks_dir / "tone.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-af", "volume=0.5", str(path)], check=True)
        value = extras.measure_lufs(path)
        self.assertIsNotNone(value)
        self.assertTrue(-30 < value < 0, value)

    def test_download_into_playlist_and_redownload(self):
        from app.music import downloader
        pl = call("playlist.create", {"name": "Online"})
        new = self.add("fresh")
        mgr = downloader.Manager(library_factory=lambda: self.lib, state_path=self.data / "jobs.json", on_track_added=lambda t: None)
        job = downloader.Job("https://soundcloud.com/a/b", "download", "", {"source": "soundcloud", "playlist_id": pl["id"]})
        job.track_ids = [new["id"]]
        mgr._after_done(job)
        self.assertEqual(call("playlist.get", {"id": pl["id"]})["count"], 1)
        old = self.add("old", source_url="https://soundcloud.com/a/b")
        job = downloader.Job("https://soundcloud.com/a/b", "download", "", {"source": "soundcloud", "replace_id": old["id"]})
        job.track_ids = [new["id"]]
        mgr._after_done(job)
        self.assertIsNone(self.lib.row(old["id"]))
        self.assertEqual(self.lib.row(new["id"])["title"], "old")
        with self.assertRaises(ValueError):
            call("library.redownload", {"id": new["id"]})

class ThumbProxyTest(Base):
    def test_cover_search_previews_go_through_our_server(self):
        from app.music import api
        client = FakeClient([("https://itunes.apple.com/search", FakeResponse(data={"results": [{"trackName": "T", "artistName": "A",
                                 "collectionName": "C", "artworkUrl100": "https://img.example/100x100bb.jpg"}]})),
                             ("https://api.deezer.com/search", FakeResponse(data={"data": []}))])
        with patch("app.music.sources._client", return_value=client):
            found = api.call("cover.search", {"q": "a t"}, base="http://127.0.0.1:9")
        thumb = found[0]["thumb"]
        self.assertTrue(thumb.startswith("http://127.0.0.1:9/api/pg/music/thumb?u=https%3A%2F%2Fimg.example%2F300x300bb.jpg&t="), thumb)
        self.assertTrue(api.is_public_media_path("/api/pg/music/thumb"))
        img = FakeResponse(content=png_bytes())
        img.headers = {"content-type": "image/png"}
        token = thumb.split("&t=")[1]
        with patch("app.music.sources._client", return_value=FakeClient([("https://img.example/", img)])):
            ok = api.thumb_image("https://img.example/300x300bb.jpg", token)
            self.assertEqual((ok.status_code, ok.media_type), (200, "image/png"))
            self.assertEqual(api.thumb_image("https://evil.example/x.png", token).status_code, 403)

class FrontTest(unittest.TestCase):
    """The feature files are wired in and syntactically valid; every i18n key used exists in ru and en."""

    FILES = ("feat-core.js", "feat-menu.js", "feat-editor.js", "feat-smart.js", "feat-settings.js", "feat-extra.js")

    def test_parts_inlined(self):
        main = (MODULE / "main.js").read_text(encoding="utf-8")
        for name in self.FILES:
            self.assertIn(f"/* @memu-part:{name} */", main)
            self.assertTrue((MODULE / name).is_file(), name)
        self.assertIn("/* @memu-part:feat.css */", (MODULE / "style.css").read_text(encoding="utf-8"))
        from app import modules_host
        with patch.dict(os.environ, {"MEMUAI_DATA_DIR": "/tmp/r38-feat-front"}):
            pkg = modules_host.load_package(Path("/tmp/r38-feat-front/modules"), "music")
        self.assertNotIn("/* @memu-part:", pkg["js"])
        self.assertNotIn("/* @memu-part:", pkg["css"])
        self.assertIn(".mm-ed-", pkg["css"])

    def test_i18n_keys(self):
        i18n = json.loads((MODULE / "i18n.json").read_text(encoding="utf-8"))
        ru, en = i18n["ru"], i18n["en"]
        used = set()
        for name in self.FILES:
            used |= set(re.findall(r"\bt\(\"([a-z0-9_.]+)\"", (MODULE / name).read_text(encoding="utf-8")))
        used = {k for k in used if not k.endswith(".")}
        for prefix, names in (("rule.op.", ["contains", "between", "within", "older"]), ("keys.a.", ["toggle", "ab", "history"]),
                              ("rule.f.", ["added_days", "liked"]), ("ed.tab.", ["tags", "cover", "lyrics", "file"])):
            used |= {prefix + n for n in names}
        missing = sorted(k for k in used if k not in ru or k not in en)
        self.assertEqual(missing, [])

    def test_node_check(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node missing")
        for name in self.FILES:
            out = subprocess.run([node, "--check", str(MODULE / name)], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, name + out.stderr)

if __name__ == "__main__":
    unittest.main()
