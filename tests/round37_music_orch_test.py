"""Round 37 (orchestrator): ``lyrics.set`` — lyrics the user pastes in MemuMusic are kept on the PC."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path


class LyricsSetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="r37-lyrset-")
        self.old = os.environ.get("MEMUAI_DATA_DIR")
        os.environ["MEMUAI_DATA_DIR"] = self.tmp
        from app.music import api, library
        self.api, self.library = api, library
        self.lib = library.get_library()
        song = Path(self.tmp) / "song.opus"
        song.write_bytes(b"OggS" + b"\0" * 64)
        self.track = self.lib.add({"file": str(song), "title": "Song", "artist": "Artist", "duration": 30})

    def tearDown(self):
        if self.old is None:
            os.environ.pop("MEMUAI_DATA_DIR", None)
        else:
            os.environ["MEMUAI_DATA_DIR"] = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_plain_lrc_and_synced_forms_are_stored_and_served_without_network(self):
        tid = self.track["id"]
        out = self.api.call("lyrics.set", {"id": tid, "plain": "one\ntwo"})
        self.assertEqual(out["source"], "manual")
        self.assertIsNone(out["synced"])
        got = self.api.call("lyrics.get", {"id": tid})
        self.assertEqual(got["plain"], "one\ntwo")
        self.assertTrue(got["cached"])
        out = self.api.call("lyrics.set", {"id": tid, "text": "[00:01.50]hello\n[00:03.00]world"})
        self.assertEqual(out["synced"], [[1.5, "hello"], [3.0, "world"]])
        out = self.api.call("lyrics.set", {"id": tid, "synced": [[61.25, "late"]]})
        self.assertEqual(out["synced"], [[61.25, "late"]])
        self.assertEqual(self.api.call("lyrics.set", {"id": tid, "text": ""})["source"], "")
        self.assertIsNone(self.lib.cached_lyrics(tid))

    def test_unknown_track_is_refused(self):
        with self.assertRaises(Exception):
            self.api.call("lyrics.set", {"id": "nope", "text": "x"})

    def test_op_is_listed(self):
        self.assertIn("lyrics.set", self.api.OPS)


if __name__ == "__main__":
    unittest.main()
