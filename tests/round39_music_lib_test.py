# -*- coding: utf-8 -*-
"""Round 39, MemuMusic library (r39-music-lib): sorting, manual order, editable artists/albums, front parts. No network."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from round37_music_core_test import Base, FakeClient, FakeResponse, png_bytes  # noqa: E402

MODULE = ROOT / "app" / "module_builtins" / "music"


def call(op, args=None):
    from app.music import api
    return api.call(op, args or {})


class SortAndOrderTest(Base):
    def add(self, name, **fields):
        return self.lib.add({"file": str(self.make_file(name + ".m4a", 3000)), "title": name, **fields})

    def test_newest_first_by_default_and_any_field_with_direction(self):
        a = self.add("Alpha", artist="Zed", added_at=time.time() - 300, duration=30, year="1999")
        b = self.add("Beta", artist="Amy", added_at=time.time() - 100, duration=90, year="2020")
        c = self.add("Gamma", artist="Mia", added_at=time.time(), duration=60, year="2010")
        ids = lambda res: [t["id"] for t in res["tracks"]]
        self.assertEqual(ids(call("library.list")), [c["id"], b["id"], a["id"]])
        self.assertEqual(ids(call("library.list", {"sort": "added", "dir": "asc"})), [a["id"], b["id"], c["id"]])
        self.assertEqual(ids(call("library.list", {"sort": "title:desc"})), [c["id"], b["id"], a["id"]])
        self.assertEqual(ids(call("library.list", {"sort": "artist"})), [b["id"], c["id"], a["id"]])
        self.assertEqual(ids(call("library.list", {"sort": "year"})), [b["id"], c["id"], a["id"]])
        self.assertEqual(ids(call("library.list", {"sort": "duration", "dir": "asc"})), [a["id"], c["id"], b["id"]])
        self.assertEqual(ids(call("library.list", {"sort": "nonsense"})), [c["id"], b["id"], a["id"]])

    def test_sort_views_setting_is_kept(self):
        saved = call("settings.set", {"sort_views": {"library": "title:asc", "liked": "added:desc"}})
        self.assertEqual(saved["sort_views"], {"library": "title:asc", "liked": "added:desc"})
        self.assertEqual(call("settings.get")["sort_views"]["library"], "title:asc")

    def test_playlist_reorder_move_and_full_order(self):
        t = [self.add(n) for n in ("One", "Two", "Three", "Four")]
        pl = call("playlist.create", {"name": "P", "track_ids": [x["id"] for x in t]})
        got = call("playlist.reorder", {"id": pl["id"], "ids": [t[3]["id"]], "to": 0})
        self.assertEqual([x["title"] for x in got["tracks"]], ["Four", "One", "Two", "Three"])
        got = call("playlist.reorder", {"id": pl["id"], "ids": [t[3]["id"]], "to": -1})
        self.assertEqual([x["title"] for x in got["tracks"]], ["One", "Two", "Three", "Four"])
        got = call("playlist.reorder", {"id": pl["id"], "order": [t[2]["id"], t[0]["id"]]})   # stale client: rest kept
        self.assertEqual([x["title"] for x in got["tracks"]], ["Three", "One", "Two", "Four"])
        with self.assertRaises(ValueError):
            call("playlist.reorder", {"id": "smart:recent", "order": []})

    def test_playlists_manual_order_new_first(self):
        p1 = call("playlist.create", {"name": "A"})
        p2 = call("playlist.create", {"name": "B"})
        p3 = call("playlist.create", {"name": "C"})
        call("playlists.reorder", {"ids": [p1["id"], p3["id"], p2["id"]]})
        plain = [p["id"] for p in call("playlists.list") if not p["smart"]]
        self.assertEqual(plain, [p1["id"], p3["id"], p2["id"]])
        p4 = call("playlist.create", {"name": "D"})
        plain = [p["id"] for p in call("playlists.list") if not p["smart"]]
        self.assertEqual(plain, [p4["id"], p1["id"], p3["id"], p2["id"]])


class ArtistAlbumTest(Base):
    def add(self, name, **fields):
        return self.lib.add({"file": str(self.make_file(name + ".m4a", 3000)), "title": name, **fields})

    def test_rename_artist_everywhere_and_merge(self):
        a = self.add("S1", artist="Old Name", album="X", album_artist="Old Name")
        self.add("S2", artist="old name")
        self.add("S3", artist="New Name")
        res = call("artist.rename", {"name": "Old Name", "new_name": "New Name"})
        self.assertTrue(res["merged"])
        self.assertEqual(res["tracks"], 2)
        names = {x["name"]: x["tracks"] for x in call("artists.list")}
        self.assertEqual(names, {"New Name": 3})
        self.assertEqual(call("library.get", {"id": a["id"]})["album_artist"], "New Name")
        with self.assertRaises(ValueError):
            call("artist.rename", {"name": "New Name", "new_name": "  "})
        with self.assertRaises(KeyError):
            call("artist.rename", {"name": "Nobody", "new_name": "X"})

    def test_artist_photo_and_bio_follow_rename(self):
        import base64
        self.add("S1", artist="Band")
        data = "data:image/png;base64," + base64.b64encode(png_bytes()).decode()
        got = call("artist.image", {"name": "Band", "data": data})
        self.assertTrue(got["image"].startswith("data:image/jpeg;base64,"))
        call("artist.bio", {"name": "band", "bio": "  Formed in 2001.  "})
        call("artist.rename", {"name": "Band", "new_name": "The Band"})
        info = call("artist.get", {"name": "The Band"})
        self.assertTrue(info["image"])
        self.assertEqual(info["bio"], "Formed in 2001.")
        self.assertTrue(next(x for x in call("artists.list") if x["name"] == "The Band")["image"])
        cleared = call("artist.image", {"name": "The Band", "clear": True})
        self.assertIsNone(cleared["image"])

    def test_artist_image_search_and_auto_with_fake_deezer(self):
        from app.music import artists
        self.add("S1", artist="Daft Punk")
        payload = {"data": [{"name": "Daft Punk", "nb_fan": 5, "picture_xl": "https://e-cdns-images.dzcdn.net/images/artist/abc/1000x1000.jpg",
                             "picture_medium": "https://e-cdns-images.dzcdn.net/images/artist/abc/250x250.jpg"},
                            {"name": "Empty", "picture_xl": "https://e-cdns-images.dzcdn.net/images/artist//1000x1000.jpg"}]}

        fake = lambda: FakeClient([("https://api.deezer.com/search/artist", FakeResponse(data=payload)),
                                   ("https://e-cdns-images.dzcdn.net/", FakeResponse(content=png_bytes()))])
        found = artists.image_search("daft", client_factory=fake)
        self.assertEqual([x["name"] for x in found], ["Daft Punk"])
        got = artists.image_auto(self.lib, "Daft Punk", client_factory=fake)
        self.assertTrue(got["found"] and got["image"])
        # once per artist: a cleared photo is not fetched again
        artists.set_image(self.lib, "Daft Punk", {"clear": True})
        spy = FakeClient([])
        again = artists.image_auto(self.lib, "Daft Punk", client_factory=lambda: spy)
        self.assertFalse(again["found"])
        self.assertEqual(spy.calls, [])

    def test_album_update_title_and_cover(self):
        import base64
        t = [self.add(n, artist="A", album="Old") for n in ("1", "2")]
        data = "data:image/png;base64," + base64.b64encode(png_bytes()).decode()
        res = call("album.update", {"ids": [x["id"] for x in t], "title": "New", "cover": {"data": data}})
        self.assertEqual({x["album"] for x in res["tracks"]}, {"New"})
        self.assertTrue(all(x["cover"] for x in res["tracks"]))
        with self.assertRaises(ValueError):
            call("album.update", {"ids": [t[0]["id"]], "title": ""})


class FrontTest(unittest.TestCase):
    def read(self, name):
        return (MODULE / name).read_text(encoding="utf-8")

    def test_parts_are_valid_js_and_included(self):
        main = self.read("main.js")
        self.assertIn("/* @memu-part:feat-lib.js */", main)
        for name in ("feat-lib.js", "views.js", "feat-menu.js", "np.js"):
            res = subprocess.run(["node", "--check", str(MODULE / name)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, name + res.stderr)

    def test_i18n_has_ru_and_en_for_new_keys_and_no_technical_notes(self):
        data = json.loads(self.read("i18n.json"))
        ru, en = data["ru"], data["en"]
        used = set(re.findall(r'\bt\("((?:sort|artist|album|move|r39)\.[a-z0-9_.]*[a-z0-9_])"', self.read("feat-lib.js")))
        used |= {"sort.f." + f for f in ("pos", "added", "title", "name", "artist", "album", "duration", "plays", "year", "liked", "updated", "count", "tracks")}
        self.assertTrue(used)
        for key in used:
            self.assertIn(key, ru, key)
            self.assertIn(key, en, key)
        joined = " ".join(map(str, ru.values())) + " ".join(map(str, en.values()))
        for phrase in ("Тон голоса не меняется", "pitch is kept", "работает через"):
            self.assertNotIn(phrase, joined)


if __name__ == "__main__":
    unittest.main()
