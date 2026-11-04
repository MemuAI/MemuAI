# -*- coding: utf-8 -*-
"""MemuMusic: lyrics for a streamed («Listen») track. The id is ``online:…``; the same sources and ranking are used,
variants/offset/pasted text/romanization work, and nothing is written to the library (in-memory LRU only)."""
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from round39_music_lyrics_test import Client, Resp, routes, LRC

TRACK = {"id": "online:abc123defgh", "title": "Numb (Official Video)", "artist": "Linkin Park - Topic", "duration": 187,
         "url": "https://music.youtube.com/watch?v=abc123"}


class OnlineLyrics(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_env = os.environ.get("MEMUAI_DATA_DIR")
        os.environ["MEMUAI_DATA_DIR"] = self.tmp.name
        from app.music import api, lyrics
        self.api, self.lyrics = api, lyrics
        self.client = Client(routes())
        self.old_client = lyrics._default_client
        lyrics._default_client = lambda: self.client
        with lyrics._online_lock:
            lyrics._online.clear()

    def tearDown(self):
        self.lyrics._default_client = self.old_client
        with self.lyrics._online_lock:
            self.lyrics._online.clear()
        from app.music import library
        with library._LIBS_LOCK:
            lib = library._LIBS.pop(str(library.data_root()), None)
        if lib is not None:
            lib.close()
        if self.old_env is None:
            os.environ.pop("MEMUAI_DATA_DIR", None)
        else:
            os.environ["MEMUAI_DATA_DIR"] = self.old_env
        self.tmp.cleanup()

    def stored_rows(self):
        db = Path(self.tmp.name, "music", "library.sqlite")
        self.assertTrue(db.is_file())
        with closing(sqlite3.connect(str(db))) as conn:
            return conn.execute("SELECT COUNT(*) FROM lyrics").fetchone()[0]

    def test_first_request_needs_the_title_then_answers_from_memory(self):
        with self.assertRaises(KeyError):
            self.api.call("lyrics.get", {"id": TRACK["id"]})
        res = self.api.call("lyrics.get", TRACK)
        self.assertTrue(res["online"])
        self.assertFalse(res["cached"])
        self.assertTrue(res["synced"] and res["synced"][0][1])
        self.assertGreaterEqual(len(res["alts"]), 2)
        calls = len(self.client.calls)
        self.assertGreater(calls, 0)
        again = self.api.call("lyrics.get", {"id": TRACK["id"]})
        self.assertTrue(again["cached"])
        self.assertEqual(again["synced"], res["synced"])
        self.assertEqual(len(self.client.calls), calls)
        self.assertEqual(self.stored_rows(), 0)

    def test_variants_offset_pasted_text_and_romanization(self):
        first = self.api.call("lyrics.get", TRACK)
        nxt = self.api.call("lyrics.choose", {"id": TRACK["id"], "next": True})
        self.assertEqual(nxt["choice"], 1 % len(first["alts"]))
        picked = self.api.call("lyrics.choose", {"id": TRACK["id"], "index": 0})
        self.assertEqual(picked["choice"], 0)
        self.assertEqual(self.api.call("lyrics.offset", {"id": TRACK["id"], "offset": 99})["offset"], 30.0)
        self.assertEqual(self.api.call("lyrics.offset", {"id": TRACK["id"], "offset": "-1.25"})["offset"], -1.25)
        self.assertEqual(self.api.call("lyrics.get", {"id": TRACK["id"]})["offset"], -1.25)

        manual = self.api.call("lyrics.set", {"id": TRACK["id"], "text": "[00:01.00]こんにちは\n[00:03.00]Second line"})
        self.assertEqual(manual["source"], "manual")
        self.assertEqual([row[1] for row in manual["synced"]], ["こんにちは", "Second line"])
        roman = self.api.call("lyrics.romanize", {"id": TRACK["id"], "language": "auto"})
        self.assertEqual([row[1] for row in roman["synced"]], ["konnichiha", "Second line"])
        cleared = self.api.call("lyrics.set", {"id": TRACK["id"], "text": ""})
        self.assertIsNone(cleared["synced"])
        self.assertNotEqual(self.api.call("lyrics.get", {"id": TRACK["id"]})["source"], "manual")
        synced_rows = self.api.call("lyrics.set", {"id": TRACK["id"], "synced": [[2.5, "Line A"], [5, "Line B"]]})
        self.assertEqual([row[1] for row in synced_rows["synced"]], ["Line A", "Line B"])
        self.assertEqual(self.stored_rows(), 0)

    def test_pasted_text_for_a_new_stream_needs_its_title(self):
        other = "online:7777777"
        with self.assertRaises(KeyError):
            self.api.call("lyrics.set", {"id": other, "text": "Hello"})
        res = self.api.call("lyrics.set", {"id": other, "text": "Hello\nWorld", "title": "Hello", "artist": "Someone"})
        self.assertEqual(res["plain"], "Hello\nWorld")
        self.assertEqual(self.api.call("lyrics.get", {"id": other})["source"], "manual")

    def test_auto_lyrics_off_waits_for_an_explicit_request(self):
        self.api.call("settings.set", {"auto_lyrics": False})
        res = self.api.call("lyrics.get", TRACK)
        self.assertTrue(res.get("auto_off"))
        self.assertEqual(self.client.calls, [])
        forced = self.api.call("lyrics.get", {**TRACK, "force": True})
        self.assertTrue(forced["synced"])

    def test_unreachable_sources_raise_and_misses_are_cached_briefly(self):
        self.client.routes = []
        failing = Client([("https://", lambda url, params: (_ for _ in ()).throw(OSError("offline")))])
        self.lyrics._default_client = lambda: failing
        with self.assertRaises(RuntimeError):
            self.api.call("lyrics.get", TRACK)
        empty = Client([("https://lrclib.net/api/search", Resp(200, [])), ("https://lrclib.net/api/get", Resp(404, {}))])
        self.lyrics._default_client = lambda: empty
        self.api.call("settings.set", {"lyrics_sources": ["lrclib"]})
        miss = self.api.call("lyrics.get", TRACK)
        self.assertFalse(miss["synced"] or miss["plain"])
        calls = len(empty.calls)
        self.assertTrue(self.api.call("lyrics.get", {"id": TRACK["id"]})["cached"])
        self.assertEqual(len(empty.calls), calls)

    def test_memory_is_bounded(self):
        old = self.lyrics.ONLINE_MAX
        self.lyrics.ONLINE_MAX = 3
        try:
            for n in range(5):
                self.api.call("lyrics.set", {"id": f"online:track{n}", "text": f"Text {n}", "title": f"T{n}", "artist": "A"})
            self.assertEqual(list(self.lyrics._online), ["online:track2", "online:track3", "online:track4"])
            with self.assertRaises(KeyError):
                self.api.call("lyrics.choose", {"id": "online:track0", "next": True})
        finally:
            self.lyrics.ONLINE_MAX = old

    def test_library_ids_keep_the_library_path(self):
        self.assertFalse(self.lyrics.is_online("online:"))
        self.assertFalse(self.lyrics.is_online("abc"))
        with self.assertRaises(KeyError):
            self.api.call("lyrics.get", {"id": "missing-track", "title": "X", "artist": "Y"})


if __name__ == "__main__":
    unittest.main()
