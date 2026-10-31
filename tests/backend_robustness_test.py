"""Backend robustness (round 42 audit): untrusted numbers must not crash the call.

Values from JSON args, settings files, provider replies and chat files on disk
("abc", lists, "12.5", "3:30") fall back to a default instead of raising
ValueError/TypeError into a 500. Search walk keeps its rows and skips the
well-known junk directories.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# This VM has no fastapi/httpx; the real environment does. Stub only what is
# missing so the module imports under test stay the same either way.
try:
    import fastapi  # noqa: F401
except ImportError:
    _fa = types.ModuleType("fastapi")
    _fa.Request = object
    _fa.FastAPI = object
    _fa.APIRouter = object
    sys.modules["fastapi"] = _fa
    _fr = types.ModuleType("fastapi.responses")
    _fr.JSONResponse = object
    _fr.Response = object
    _fr.StreamingResponse = object
    _fr.FileResponse = object
    _fr.PlainTextResponse = object
    sys.modules["fastapi.responses"] = _fr
try:
    import httpx  # noqa: F401
except ImportError:
    _hx = types.ModuleType("httpx")
    _hx.Client = object
    _hx.AsyncClient = object
    _hx.Timeout = object
    sys.modules["httpx"] = _hx

from app import chat_tree as ct  # noqa: E402
from app.api_budget import _counts, view  # noqa: E402
from app.memufiles.search import Query, walk_search  # noqa: E402
from app.music.library import Library  # noqa: E402


class LibraryCoercionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "music"
        self.lib = Library(self.root)
        self.song = Path(self.tmp.name) / "song.mp3"
        self.song.write_bytes(b"\xff\xfb" + b"\x00" * 100)
        self.track_id = self.lib.add({"file": str(self.song), "title": "T", "artist": "A"})["id"]

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_history_stats_accept_garbage_numbers(self):
        self.assertEqual(self.lib.list(limit="abc")["total"], 1)
        self.assertEqual(self.lib.list(offset=[1])["total"], 1)
        self.assertEqual(self.lib.list(limit="2")["total"], 1)
        self.assertEqual(self.lib.history(limit="x"), [])
        stats = self.lib.stats(days="abc")
        self.assertEqual(stats["tracks"], 1)
        self.assertEqual(stats["days"], 30)

    def test_played_and_add_accept_garbage_numbers(self):
        self.assertTrue(self.lib.played(self.track_id, "abc")["ok"])
        self.assertTrue(self.lib.played(self.track_id, [5])["ok"])
        row = self.lib.add({"file": str(self.song), "title": "T2", "bitrate": "128k", "duration": "3:30"})
        self.assertEqual(row["bitrate"], 0)
        self.assertEqual(row["duration"], 0.0)

    def test_smart_ids_survive_corrupt_settings(self):
        (self.root / "settings.json").write_text(
            json.dumps({"smart_limit": "abc", "recent_days": "zz"}), encoding="utf-8")
        self.assertIn(self.track_id, self.lib._smart_ids("smart:recent"))
        self.assertEqual(self.lib._smart_ids("smart:top"), [])
        self.assertEqual(self.lib._smart_ids("smart:liked"), [])

    def test_volume_setting_rejects_garbage_with_value_error(self):
        with self.assertRaises(ValueError):
            self.lib.set_settings({"volume": "abc"})


class ChatTreeCorruptTest(unittest.TestCase):
    def test_corrupt_sel_and_alts_do_not_crash(self):
        chat = {"messages": [{"id": "m-1", "role": "user", "content": "hi"}],
                "branches": {"v": 1, "forks": {"m-1": {"sel": "oops", "alts": [
                    {"id": "v-1", "at": 1, "m": None}, {"id": "v-2", "at": 2, "m": []}]}}}}
        self.assertTrue(ct.switch_to(chat, "m-1", 1))
        self.assertTrue(ct.normalize(chat))
        chat3 = {"messages": [{"id": "m-1"}],
                 "branches": {"v": 1, "forks": {"m-1": {"sel": 0, "alts": ["not-a-dict", {"id": "v-2", "m": None}]}}}}
        self.assertFalse(ct.switch_to(chat3, "m-1", 1))

    def test_begin_version_with_garbage_created_at(self):
        chat = {"messages": [{"id": "m-1", "createdAt": "not-a-time", "content": "hi"}]}
        self.assertEqual(ct.begin_version(chat, 0), "^")
        self.assertEqual(chat["messages"], [])


class ApiBudgetTest(unittest.TestCase):
    def test_counts_accept_float_strings_and_lists(self):
        counts = _counts({"prompt_tokens": "12.5", "completion_tokens": 3})
        self.assertEqual(counts["input_tokens"], 12)
        self.assertEqual(counts["output_tokens"], 3)
        self.assertEqual(_counts({"prompt_tokens": [1]})["input_tokens"], 0)

    def test_view_survives_corrupt_saved_numbers(self):
        try:
            import app.state  # noqa: F401
        except Exception:
            fake = types.ModuleType("app.state")

            class _State:
                _lock = threading.RLock()
                models = {}
                accounts = {}

            fake.state = _State()
            sys.modules["app.state"] = fake
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.environ["MEMUAI_DATA_DIR"] = tmp.name
        self.addCleanup(lambda: os.environ.pop("MEMUAI_DATA_DIR", None))
        data_dir = Path(tmp.name) / "memuai"
        data_dir.mkdir()
        (data_dir / "api-budgets.json").write_text(json.dumps(
            {"version": 1, "accounts": {"a1": {"balance_usd": "abc", "spent_usd": "5",
                                               "started_at": "zz", "requests": "3", "until": "bad",
                                               "prefix": "x", "provider": "x", "label": "X", "models": {}}}}),
            encoding="utf-8")
        import app.api_budget as budget
        budget._cached_stamp = None
        acct = SimpleNamespace(id="a1", label="X", provider="x", enabled=True)
        row = view([acct])["accounts"][0]
        self.assertEqual(row["spent_usd"], 5.0)
        self.assertEqual(row["remaining_usd"], -5.0)


class SearchWalkTest(unittest.TestCase):
    def test_skips_junk_dirs_and_keeps_rows(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        (base / "node_modules" / "pkg").mkdir(parents=True)
        (base / "node_modules" / "pkg" / "hit.txt").write_text("x")
        (base / ".git").mkdir()
        (base / ".git" / "hit.txt").write_text("x")
        (base / "docs").mkdir()
        (base / "docs" / "hit.txt").write_text("x")
        rows = list(walk_search(str(base), Query("hit"), lambda: False, limit=100))
        self.assertEqual([r["n"] for r in rows], ["hit.txt"])
        self.assertGreater(rows[0]["m"], 0)
        self.assertEqual(list(walk_search(str(base), Query("hit"), lambda: False, limit=1)), rows[:1])


class MusicApiArgsTest(unittest.TestCase):
    def test_arg_helpers_and_limits(self):
        from app.music import api as music_api
        from app.music import online, downloader
        self.assertEqual(music_api._arg_int("3", 0), 3)
        self.assertEqual(music_api._arg_int("abc", 7), 7)
        self.assertEqual(music_api._arg_int([1], 7), 7)
        self.assertEqual(music_api._arg_float("1.5", 0.0), 1.5)
        self.assertEqual(music_api._arg_float("zz", 2.0), 2.0)
        self.assertEqual(music_api._synced_line("bad", "la"), "")
        self.assertEqual(music_api._synced_line(65, "la"), "[01:05.00]la")
        self.assertEqual(online._limit("abc", 12), 12)
        self.assertEqual(downloader._limit("20", 12), 20)

    def test_playlist_reorder_to_coercion(self):
        from app.music import artists
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        lib = Library(Path(tmp.name) / "music")
        song = Path(tmp.name) / "s.mp3"
        song.write_bytes(b"\x00" * 10)
        ids = [lib.add({"file": str(song), "title": f"T{i}"})["id"] for i in range(3)]
        pl = lib.create_playlist("P", ids)
        with self.assertRaises(ValueError):
            artists.reorder_playlist(lib, pl["id"], {"ids": [ids[0]], "to": "abc"})
        out = artists.reorder_playlist(lib, pl["id"], {"ids": [ids[0]], "to": "1"})
        self.assertEqual([t["id"] for t in out["tracks"]], [ids[1], ids[0], ids[2]])


if __name__ == "__main__":
    unittest.main()
