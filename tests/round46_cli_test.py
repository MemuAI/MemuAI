"""Read/Grep economy retains explicit ranges, fresh data and exact count mode."""
from __future__ import annotations

import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

from app.cli.file_cache import TextFiles
from app.cli.tools import files
from app.cli.tools.base import Prepared, ToolCall, ToolContext
from tests.memucode_cli_test import make_runtime, setUpModule, tearDownModule  # noqa: F401


class FileEconomy(unittest.TestCase):
    def context(self, content="line\n" * 2100):
        runtime, _ = make_runtime({"code.py": content})
        ctx = ToolContext(runtime, ToolCall("Read", {"file_path": "code.py"}))
        return ctx, runtime.root / "code.py"

    def test_default_is_bounded_and_explicit_large_ranges_remain_available(self):
        ctx, path = self.context()
        result = files.ReadTool()._read_text(ctx, path, {})
        self.assertEqual(result.meta["lines"], 300)
        self.assertIn("offset=301", result.output)
        result = files.ReadTool()._read_text(ctx, path, {"limit": 1800})
        self.assertEqual(result.meta["lines"], 1800)
        self.assertIn("offset=1801", result.output)

    def test_pagination_decodes_once_but_checks_freshness(self):
        ctx, path = self.context()
        with patch.object(files, "read_text_meta", wraps=files.read_text_meta) as decode:
            for offset in (1, 101, 201):
                files.ReadTool()._read_text(ctx, path, {"offset": offset, "limit": 30})
            self.assertEqual(decode.call_count, 1)
            path.write_text("changed\n" * 2100, "utf-8")
            result = files.ReadTool()._read_text(ctx, path, {"offset": 101, "limit": 30})
            self.assertEqual(decode.call_count, 2)
            self.assertIn("changed", result.output)

    def test_force_replays_content_and_elision_does_not_hide_needed_lines(self):
        ctx, path = self.context()
        read = files.ReadTool()
        first = read._read_text(ctx, path, {})
        self.assertTrue(read._read_text(ctx, path, {}).meta["unchanged"])
        self.assertEqual(first.output, read._read_text(ctx, path, {"force": True}).output)
        ctx.session.context_epoch += 1
        self.assertEqual(first.output, read._read_text(ctx, path, {}).output)

    def test_atomic_replacement_with_same_size_and_mtime_is_not_unchanged(self):
        ctx, path = self.context("old\n")
        read = files.ReadTool()
        read._read_text(ctx, path, {})
        old = path.stat()
        replacement = path.with_suffix(".tmp")
        replacement.write_text("new\n", "utf-8")
        os.utime(replacement, ns=(old.st_atime_ns, old.st_mtime_ns))
        os.replace(replacement, path)
        from app.cli.tools.base import ToolError
        with self.assertRaisesRegex(ToolError, "modified on disk"):
            files._check_fresh(ctx, path)
        self.assertIn("new", read._read_text(ctx, path, {}).output)
        files._check_fresh(ctx, path)

    def test_changed_during_decode_is_not_cached_as_current(self):
        ctx, path = self.context("old\n")
        original = files.read_text_meta
        def change(*args, **kwargs):
            result = original(*args, **kwargs)
            path.write_text("modified\n", "utf-8")
            return result
        with patch.object(files, "read_text_meta", side_effect=change):
            result = files.ReadTool()._read_text(ctx, path, {})
        self.assertIn("changed on disk", result.output)
        self.assertIn("modified", files.ReadTool()._read_text(ctx, path, {}).output)

    def test_snapshot_cache_is_bounded_and_thread_safe(self):
        cache = TextFiles(max_bytes=2048, max_files=2)
        def add(number):
            cache.put(str(number), (number,), ["value" * 20], False)
            cache.get(str(number), (number,))
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(add, range(100)))
        self.assertLessEqual(cache._bytes, 2048)
        self.assertLessEqual(len(cache._rows), 2)
        cache.clear()
        self.assertEqual(cache._bytes, 0)

    def test_search_existence_count_and_context_modes(self):
        ctx, path = self.context("target\nother\ntarget\n")
        ctx.runtime.files.files = lambda **kwargs: ["code.py"]
        prepared = Prepared(data={"base": ctx.runtime.root, "prefix": "", "pattern": "^target$"})
        search = files.GrepTool()
        self.assertEqual(search._search(ctx, {}, prepared).output, "code.py")
        self.assertEqual(search._search(ctx, {"output_mode": "count"}, prepared).output, "code.py:2")
        result = search._search(ctx, {"output_mode": "content", "-C": 1}, prepared)
        self.assertIn("code.py:1:target", result.output)
        self.assertIn("code.py-2-other", result.output)
        self.assertIn("code.py:3:target", result.output)

    def test_multiline_existence_matches_and_nonmatches(self):
        ctx, _ = self.context("start\nend\n")
        ctx.runtime.files.files = lambda **kwargs: ["code.py"]
        prepared = Prepared(data={"base": ctx.runtime.root, "prefix": "", "pattern": "start.*end"})
        result = files.GrepTool()._search(ctx, {"multiline": True}, prepared)
        self.assertEqual(result.output, "code.py")
        self.assertIn("No matches", files.GrepTool()._search(ctx, {}, prepared).output)


if __name__ == "__main__":
    unittest.main()
