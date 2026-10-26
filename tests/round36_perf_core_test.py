"""Round 36 (perf-core): MemuAI's own process — file index memory, lazy start, installer block cache, WebView2 flags.

No network, no Windows needed."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEMUAI_DATA_DIR", tempfile.mkdtemp(prefix="r36-perf-core-"))
os.environ["MEMUAI_FSINDEX_NOSTART"] = "1"

from app import fsindex, fsindex_query as fq, webview_flags  # noqa: E402
from installer import payload  # noqa: E402

def legacy_rows():
    return [
        ("C:\\Users\\Ann\\Docs", [("Zeta.txt", 10, 1.5, False), ("alpha.PDF", 20, 2.5, False), ("Ёлка", 0, 3.0, True),
                                  ("bad\nname", 1, 1.0, False)]),
        ("C:\\Users\\Ann", [("Docs", 0, 1.0, True), ("notes.md", 5, 4.0, False)]),
        ("C:\\Users\\Ann\\Docs", [("beta.txt", 7, 5.0, False)]),
        ("C:\\Empty", []),
    ]

class PackedTest(unittest.TestCase):
    def test_packed_iterates_like_the_rows_it_was_made_of_sorted(self):
        p = fsindex.Packed([("b.txt", 1, 2.0, False), ("A.txt", 3, 4.0, True), ("x\ny", 1, 1.0, False)])
        self.assertEqual(len(p), 2)
        self.assertEqual(list(p), [("A.txt", 3, 4.0, True), ("b.txt", 1, 2.0, False)])
        self.assertEqual(list(fsindex.Packed([])), [])

    def test_snapshot_from_packed_equals_snapshot_from_tuples(self):
        a = fsindex.Snapshot.build(legacy_rows(), ["C:\\"])
        b = fsindex.Snapshot.build([(f, fsindex.Packed(k)) for f, k in legacy_rows()], ["C:\\"])
        for attr in ("blob", "lblob", "starts", "parent", "size", "mtime", "isdir", "byname", "dirs", "dkeys"):
            self.assertEqual(getattr(a, attr), getattr(b, attr), attr)
        self.assertEqual(len(a), 6)
        self.assertGreaterEqual(a.find("C:\\Users\\Ann\\Docs\\beta.txt"), 0)
        self.assertEqual(a.find("C:\\Users\\Ann\\Docs\\bad\nname"), -1)
        self.assertFalse(hasattr(a, "dsort"), "no third copy of the folder paths")
        lo, hi = a.subtree("c:/users/ann/docs")
        self.assertEqual(sorted(a.name(i) for i in range(lo, hi)), ["Zeta.txt", "alpha.PDF", "beta.txt", "Ёлка"])

    def test_walker_produces_packed_batches_and_search_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            for rel in ("a/Report.docx", "a/b/report-2.txt", "a/b/c/photo.JPG"):
                path = Path(tmp, rel)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x")
            batches = fsindex.walk([tmp], fsindex.Exclusions([]))
            self.assertTrue(all(isinstance(kids, fsindex.Packed) for _f, kids in batches))
            idx = fsindex.Index(fsindex.Snapshot.build(batches, [tmp]))
            got = idx.search(fq.parse("report"), limit=10)
            self.assertEqual({r["name"] for r in got["results"]}, {"Report.docx", "report-2.txt"})
            self.assertEqual(fsindex.Snapshot.build(idx.live_batches(), [tmp]).blob, idx.snap.blob)

class LazyStartTest(unittest.TestCase):
    def test_preload_is_off_by_default_and_normalized(self):
        self.assertIs(fsindex.DEFAULT_SETTINGS["preload"], False)
        self.assertIs(fsindex.normalize_settings({})["preload"], False)
        self.assertIs(fsindex.normalize_settings({"preload": "on"})["preload"], True)

    def test_warm_thread_starts_nothing_without_preload(self):
        src = (ROOT / "app" / "fsindex_api.py").read_text(encoding="utf-8")
        self.assertIn('settings.get("preload")', src)
        self.assertIn('"/api/fsindex/warm"', src)
        js = (ROOT / "app" / "pgstatic" / "quick" / "plugins" / "fsearch.js").read_text(encoding="utf-8")
        self.assertIn("/api/fsindex/warm", js)

class PayloadCacheTest(unittest.TestCase):
    def test_unchanged_blocks_are_reused_and_the_payload_is_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for comp in ("one", "two"):
                folder = base / comp
                folder.mkdir()
                for n in range(6):
                    (folder / f"f{n}.dll").write_bytes(os.urandom(3000) + bytes(5000))
                    (folder / f"t{n}.txt").write_text(f"{comp} {n} " * 300)
            sources = [("one", base / "one", "One"), ("two", base / "two", "Two")]
            stub = base / "stub.exe"
            stub.write_bytes(b"MZ" + bytes(1000))
            cache = base / "cache"
            logs = []

            def build(name):
                out = base / name
                payload.assemble(stub, sources, {"product": "t"}, out, preset=1, workers=2, block_target=8000,
                                 cache_dir=cache, log=logs.append)
                with payload.Payload(out) as pak:
                    return {e.path: b"".join(c) for e, c in pak.iter_files(pak.index.components())}

            first = build("a.exe")
            n_blocks = len(list(cache.glob("*.xz")))
            self.assertGreater(n_blocks, 2)
            self.assertIn("block cache: 0/", logs[-1])
            second = build("b.exe")
            self.assertEqual(first, second)
            self.assertIn(f"block cache: {n_blocks}/{n_blocks}", logs[-1])
            self.assertEqual((base / "a.exe").read_bytes(), (base / "b.exe").read_bytes())
            (base / "two" / "t3.txt").write_text("changed")
            third = build("c.exe")
            self.assertEqual(third["Two/t3.txt"], b"changed")
            reused = int(logs[-1].split(": ")[1].split("/")[0])
            self.assertGreater(reused, 0)
            self.assertLess(reused, n_blocks)
            self.assertEqual(len(list(cache.glob("*.xz"))), n_blocks, "stale blocks are pruned")

class WebViewFlagsTest(unittest.TestCase):
    def test_arguments_keep_pywebview_switch(self):
        args = webview_flags.arguments()
        self.assertIn("ElasticOverscroll", args)
        self.assertIn("SpareRendererForSitePerProcess", args)
        self.assertIn("--remote-debugging-port=9222", webview_flags.arguments(9222))

    def test_apply_only_on_windows_and_never_over_a_user_value(self):
        saved = os.environ.pop(webview_flags.ENV, None)
        try:
            self.assertFalse(webview_flags.apply("linux"))
            self.assertNotIn(webview_flags.ENV, os.environ)
            self.assertTrue(webview_flags.apply("win32"))
            self.assertIn("--disable-features=", os.environ[webview_flags.ENV])
            os.environ[webview_flags.ENV] = "--user"
            self.assertFalse(webview_flags.apply("win32"))
            self.assertEqual(os.environ[webview_flags.ENV], "--user")
        finally:
            os.environ.pop(webview_flags.ENV, None)
            if saved is not None:
                os.environ[webview_flags.ENV] = saved

if __name__ == "__main__":
    unittest.main()
