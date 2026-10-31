"""Extended MemuMusic module commands (music.search / radio / like / queue / playlists / settings).

Unit part only: ``music_api.call`` is mocked, so no library, player or network is
touched. The mock filters ``library.list`` by the query, like the real library,
so a name matching several tracks stays ambiguous here too.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import module_integration as mi

TRACKS = [
    {"id": "t1", "title": "Believer", "artist": "Imagine Dragons", "album": "Evolve", "duration": 204, "liked": False, "cover": "", "accent": ""},
    {"id": "t2", "title": "Thunder", "artist": "Imagine Dragons", "album": "Evolve", "duration": 187, "liked": False, "cover": "", "accent": ""},
    {"id": "t3", "title": "Numb", "artist": "Linkin Park", "album": "Meteora", "duration": 187, "liked": False, "cover": "", "accent": ""},
]
PLAYLISTS = [{"id": "pl-aaa", "name": "Утро", "count": 2, "cover": ""},
             {"id": "pl-bbb", "name": "Вечер", "count": 1, "cover": ""}]


def _fake_call(op, args=None, **kwargs):
    args = args or {}
    if op == "library.list":
        q = str(args.get("q") or "").lower()
        rows = [t for t in TRACKS if not q or q in f"{t['title']} {t['artist']} {t['album']}".lower()]
        return {"tracks": rows[: int(args.get("limit") or 200)], "total": len(rows)}
    if op == "library.get":
        for track in TRACKS:
            if track["id"] == args.get("id"):
                return dict(track)
        raise KeyError("Трек не найден / Track not found")
    if op == "library.like":
        return {"ok": True, "id": args.get("id"), "liked": bool(args.get("liked", True))}
    if op == "library.radio":
        return {"seed": dict(TRACKS[0]), "tracks": [dict(TRACKS[0]), dict(TRACKS[1])]}
    if op == "library.update":
        track = dict(TRACKS[0])
        track.update({k: v for k, v in args.items() if k in ("title", "artist", "album")})
        return track
    if op == "library.delete":
        return {"ok": True, "id": args.get("id"), "file_removed": bool(args.get("delete_file"))}
    if op == "library.duplicates":
        return [{"key": "believer|imagine dragons", "tracks": [dict(TRACKS[0]), dict(TRACKS[1])]}]
    if op == "library.stats":
        return {"tracks": 3, "artists": 2, "minutes": 9.0, "listened_minutes": 5.0, "listens": 4, "liked": 1}
    if op == "playlists.list":
        return [dict(p) for p in PLAYLISTS]
    if op == "playlist.get":
        for pl in PLAYLISTS:
            if pl["id"] == args.get("id"):
                return {"id": pl["id"], "name": pl["name"], "tracks": [dict(TRACKS[0]), dict(TRACKS[1])]}
        raise KeyError("Плейлист не найден / Playlist not found")
    if op == "playlist.create":
        return {"id": "pl-new", "name": args.get("name"), "tracks": [dict(TRACKS[0])]}
    if op == "playlist.update":
        return {"id": args.get("id"), "name": "Утро", "tracks": [dict(TRACKS[0]), dict(TRACKS[1])]}
    if op == "playlist.delete":
        return {"ok": True, "id": args.get("id")}
    if op == "playlist.reorder":
        return {"id": args.get("id"), "name": "Утро", "tracks": [dict(TRACKS[1]), dict(TRACKS[0])]}
    if op == "settings.get":
        return {"volume": 0.8, "normalize": True, "crossfade": 0, "library_dir": "/secret/music",
                "default_download_dir": "/secret/downloads"}
    if op == "settings.set":
        out = {"volume": 0.8, "normalize": True}
        out.update(args)
        return out
    if op == "player.state":
        return {"track_id": "t1", "track": dict(TRACKS[0]), "queue": ["t1", "t2"], "index": 0,
                "playing": True, "position_now": 10.0, "volume": 0.8, "stale": False}
    if op == "player.command":
        return {"ok": True, "seq": 1}
    raise ValueError(f"unexpected op {op}")


class MusicCommandsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name) / "modules"
        from app import modules_host as mh
        mh.configure_module(cls.root, "music", enabled=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.calls = []
        def recording(op, args=None, **kwargs):
            self.calls.append((op, dict(args or {})))
            return _fake_call(op, args, **kwargs)
        patcher = mock.patch("app.music.api.call", side_effect=recording)
        self.addCleanup(patcher.stop)
        patcher.start()

    def run_cmd(self, command, args=None, **kwargs):
        return asyncio.run(mi.run_command("music", command, args or {}, root=self.root, **kwargs))

    def ops(self, op):
        return [args for name, args in self.calls if name == op]

    def test_manifest_has_28_commands_and_only_two_dangerous(self):
        from app import modules_host as mh
        manifest = json.loads((mh.builtin_dir() / "music" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(mi.validate_contributes(manifest["contributes"], builtin=True, folder=mh.builtin_dir() / "music"), [])
        clean = mi.normalize_contributes(manifest["contributes"], builtin=True)
        self.assertEqual(len(clean["commands"]), 28)
        new = {"search", "radio", "like", "delete_track", "seek", "shuffle", "repeat", "queue",
               "rename_track", "playlist_create", "playlist_delete", "playlist_add",
               "playlist_remove", "playlist_reorder", "settings",
               "toggle", "stop", "duplicates", "stats"}
        self.assertTrue(new <= {c["id"] for c in clean["commands"]})
        self.assertEqual({c["id"] for c in clean["commands"] if c["danger"]}, {"delete_track", "playlist_delete"})
        settings = next(c for c in clean["commands"] if c["id"] == "settings")
        keys = settings["args"]["properties"]["key"]["enum"]
        self.assertNotIn("download_dir", keys)
        self.assertNotIn("cookies_from_browser", keys)
        self.assertNotIn("hotkeys", keys)

    def test_search_radio_like_route_to_library(self):
        out = self.run_cmd("search", {"query": "Believer"})
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.ops("library.list")[0]["q"], "Believer")
        self.assertIn("Believer", out["text"])

        out = self.run_cmd("radio", {"query": "Believer"})
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.ops("library.radio")[0]["id"], "t1")
        self.assertEqual(self.ops("player.command")[-1]["cmd"], "play_track")

        out = self.run_cmd("like", {"query": "Numb"})
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.ops("library.like"), [{"id": "t3", "liked": True}])

        out = self.run_cmd("like", {"track_id": "t1", "liked": False})
        self.assertEqual(self.ops("library.like")[-1], {"id": "t1", "liked": False})

    def test_seek_shuffle_repeat_use_player_command(self):
        self.assertTrue(self.run_cmd("seek", {"position": 42})["ok"])
        self.assertIn({"cmd": "seek", "position": 42, "target": "pc"}, self.ops("player.command"))
        self.assertTrue(self.run_cmd("shuffle", {"enabled": True})["ok"])
        self.assertIn({"cmd": "shuffle", "value": True, "target": "pc"}, self.ops("player.command"))
        self.assertTrue(self.run_cmd("repeat", {"mode": "one"})["ok"])
        self.assertIn({"cmd": "repeat", "value": "one", "target": "pc"}, self.ops("player.command"))
        bad = self.run_cmd("repeat", {"mode": "forever"})
        self.assertFalse(bad["ok"])

    def test_queue_show_add_and_move(self):
        out = self.run_cmd("queue", {"action": "show"})
        self.assertTrue(out["ok"], out)
        self.assertIn("Believer", out["text"])
        self.assertIn("Thunder", out["text"])

        out = self.run_cmd("queue", {"action": "add", "track_id": "t3"})
        self.assertTrue(out["ok"], out)
        set_queue = self.ops("player.command")[-1]
        self.assertEqual(set_queue["cmd"], "set_queue")
        self.assertEqual(set_queue["queue"], ["t1", "t2", "t3"])
        self.assertEqual(set_queue["track_id"], "t1")

        out = self.run_cmd("queue", {"action": "move", "track_id": "t2", "position": 1})
        set_queue = self.ops("player.command")[-1]
        self.assertEqual(set_queue["queue"], ["t2", "t1"])

    def test_queue_add_by_ambiguous_name_lists_candidates(self):
        out = self.run_cmd("queue", {"action": "add", "query": "Imagine"})
        self.assertFalse(out["ok"])
        self.assertEqual(self.ops("player.command"), [])
        self.assertEqual(out["card"]["kind"], "list")
        self.assertIn("t1", out["text"])

    def test_rename_track_uses_exact_id(self):
        out = self.run_cmd("rename_track", {"track_id": "t1", "title": "Believer (new)"})
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.ops("library.update"), [{"id": "t1", "title": "Believer (new)"}])
        out = self.run_cmd("rename_track", {"track_id": "t1"})
        self.assertFalse(out["ok"])

    def test_playlist_create_add_remove_reorder(self):
        out = self.run_cmd("playlist_create", {"name": "Дорога", "query": "Believer"})
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.ops("playlist.create")[0]["name"], "Дорога")

        out = self.run_cmd("playlist_add", {"playlist_id": "pl-aaa", "track_id": "t3"})
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.ops("playlist.update"), [{"id": "pl-aaa", "add": ["t3"]}])

        out = self.run_cmd("playlist_remove", {"name": "Утро", "track_id": "t2"})
        self.assertEqual(self.ops("playlist.update")[-1], {"id": "pl-aaa", "remove": ["t2"]})

        out = self.run_cmd("playlist_reorder", {"playlist_id": "pl-aaa", "track_id": "t2", "position": 1})
        self.assertEqual(self.ops("playlist.reorder"), [{"id": "pl-aaa", "ids": ["t2"], "to": 0}])

    def test_playlist_delete_needs_confirmation_and_exact_name(self):
        pending = self.run_cmd("playlist_delete", {"playlist_id": "pl-aaa"})
        self.assertTrue(pending["needs_confirm"])
        self.assertEqual(self.ops("playlist.delete"), [])

        out = self.run_cmd("playlist_delete", {"name": "Утро"}, confirmed=True)
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.ops("playlist.delete"), [{"id": "pl-aaa"}])

        fuzzy = self.run_cmd("playlist_delete", {"name": "утр"}, confirmed=True)
        self.assertFalse(fuzzy["ok"])
        self.assertEqual(len(self.ops("playlist.delete")), 1)

    def test_delete_track_never_deletes_by_name_and_keeps_file_by_default(self):
        out = self.run_cmd("delete_track", {"query": "Believer"}, confirmed=True)
        self.assertFalse(out["ok"])
        self.assertEqual(self.ops("library.delete"), [])
        self.assertEqual(out["card"]["kind"], "list")

        pending = self.run_cmd("delete_track", {"track_id": "t1"})
        self.assertTrue(pending["needs_confirm"])
        self.assertEqual(self.ops("library.delete"), [])

        out = self.run_cmd("delete_track", {"track_id": "t1"}, confirmed=True)
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.ops("library.delete"), [{"id": "t1", "delete_file": False}])

        out = self.run_cmd("delete_track", {"track_id": "t1", "delete_file": True}, confirmed=True)
        self.assertEqual(self.ops("library.delete")[-1], {"id": "t1", "delete_file": True})
        self.assertIn("файл", out["text"].lower())

    def test_settings_whitelist_and_no_path_leak(self):
        out = self.run_cmd("settings", {"key": "volume"})
        self.assertTrue(out["ok"], out)
        self.assertIn("0.8", out["text"])
        self.assertNotIn("/secret", out["text"])
        self.assertEqual(self.ops("settings.get"), [{}])

        out = self.run_cmd("settings", {"key": "volume", "value": "40"})
        self.assertEqual(self.ops("settings.set"), [{"volume": 0.4}])

        for key in ("download_dir", "cookies_from_browser", "hotkeys"):
            out = self.run_cmd("settings", {"key": key, "value": "x"})
            self.assertFalse(out["ok"], key)
        self.assertEqual(self.ops("settings.set"), [{"volume": 0.4}])

        # The handler itself refuses forbidden keys even if arg checking is bypassed.
        from app.module_integration_handlers import music_settings
        ctx = {"lang": "ru", "en": False, "tr": lambda ru, en: ru, "module": {}, "root": self.root,
               "surface": "chat", "base": ""}
        with self.assertRaises(mi.CommandError):
            asyncio.run(music_settings({"key": "hotkeys", "value": "{}"}, ctx))

    def test_toggle_stop_duplicates_stats(self):
        out = self.run_cmd("toggle", {})
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.ops("player.command")[-1]["cmd"], "toggle")
        out = self.run_cmd("stop", {})
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.ops("player.command")[-1]["cmd"], "stop")
        out = self.run_cmd("duplicates", {})
        self.assertTrue(out["ok"], out)
        self.assertIn("1 групп", out["text"])
        out = self.run_cmd("stats", {})
        self.assertTrue(out["ok"], out)
        self.assertIn("3 треков", out["text"])


if __name__ == "__main__":
    unittest.main()
