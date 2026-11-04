# -*- coding: utf-8 -*-
"""MemuMusic recommendations and streamed playback in the real module (demo mode, headless Chromium):
the customize panel, a background search with progress that survives navigation, «Stop», filters, hiding a track
or an artist, «Play all» as streams, and lyrics for a streamed track (title/artist are sent with the id)."""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
import round37_music_harness as harness


class RecommendationsUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not harness.chromium():
            raise unittest.SkipTest("no Chromium")
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()

    @classmethod
    def tearDownClass(cls):
        cls.pw.stop()

    def setUp(self):
        self.browser, self.page, self.errors = harness.open_page(self.pw, "ru", "dark", (1280, 860))
        self.f = harness.frame_of(self.page)
        self.wait("() => !!(window.MemuMusic && MemuMusic.S.loaded)")
        # Every bridge call of the demo backend is recorded (API.call and API.soft both end in MockMusic.call).
        self.f.evaluate("""() => { const mock = MemuMusic.dev.MockMusic, real = mock.call.bind(mock); window.__ops = [];
          mock.call = (op, args) => { window.__ops.push([op, JSON.parse(JSON.stringify(args || {}))]);
            if (op === 'online.related' && window.__slowRelated) return new Promise((ok) => setTimeout(() => ok(real(op, args)), window.__slowRelated));
            return real(op, args); }; }""")

    def tearDown(self):
        try:
            self.assertEqual([e for e in self.errors if "favicon" not in e], [])
        finally:
            self.browser.close()

    def wait(self, script, timeout=20000):
        harness.wait(self.page, self.f, script, timeout)

    def ev(self, script):
        return self.f.evaluate(script)

    def test_customize_run_filters_hide_and_play(self):
        self.ev("() => MemuMusic.go('recommendations', {}, {instant: true})")
        self.wait("() => !!document.querySelector('.mm-rec-empty')")
        self.assertTrue(self.ev("() => document.querySelector('.mm-rec-tune').hidden"))
        self.ev("() => document.querySelector('[data-rec-tune]').click()")
        self.wait("() => !document.querySelector('.mm-rec-tune').hidden && !!document.querySelector('[data-rec-seg=depth]')")
        self.assertEqual(self.ev("() => document.querySelectorAll('[data-rec-seg=basis] [data-v]').length"), 8)
        self.ev("() => document.querySelector('[data-rec-seg=depth] [data-v=\"8\"]').click()")
        self.ev("() => document.querySelector('[data-rec-seg=perArtist] [data-v=\"0\"]').click()")
        self.ev("() => { const s = document.querySelector('[data-rec-sw=noLibrary]'); s.checked = false; s.dispatchEvent(new Event('change', {bubbles: true})); }")
        recs = self.ev("() => MemuMusic.S.prefs.recs")
        self.assertEqual((recs["depth"], recs["perArtist"], recs["noLibrary"]), (8, 0, False))
        self.assertEqual(self.ev("() => document.querySelector('[data-rec-seg=depth] [aria-checked=true]').dataset.v"), "8")
        self.assertTrue(self.ev("() => MemuMusic.S.prefs.recsTuneOpen"))

        self.page.evaluate("() => 0")
        self.ev("() => { window.__slowRelated = 250; document.querySelector('[data-rec-run]').click(); }")
        self.wait("() => !document.querySelector('[data-rec-cancel]').hidden && !document.querySelector('.mm-rec-progress').hidden")
        self.assertTrue(self.ev("() => document.querySelector('[data-rec-run]').disabled"))
        # Leaving the page does not stop the search; coming back shows its progress, then the result.
        self.ev("() => MemuMusic.go('home', {}, {instant: true})")
        self.page.wait_for_timeout(300)
        self.ev("() => MemuMusic.go('recommendations', {}, {instant: true})")
        self.wait("() => (MemuMusic.S.prefs.recommendations || []).length > 0 && document.querySelector('[data-rec-cancel]').hidden", 40000)
        self.wait("() => document.querySelectorAll('.mm-rec-list .mm-orow').length > 0")
        meta = self.ev("() => MemuMusic.S.prefs.recsMeta")
        self.assertEqual((meta["seeds"], meta["partial"]), (8, False))
        related = self.ev("() => window.__ops.filter(([op]) => op === 'online.related').length")
        self.assertEqual(related, 8)
        rows = self.ev("() => MemuMusic.S.prefs.recommendations")
        self.assertTrue(all(r.get("recSeeds") and r.get("recCount") for r in rows))
        self.assertIn("Похоже на", self.ev("() => document.querySelector('.mm-rec-list .mm-otxt .mm-rec-why').textContent"))
        self.assertIn("треков", self.ev("() => document.querySelector('[data-rec-status]').textContent"))

        counts = self.ev("() => [...document.querySelectorAll('.mm-rec-filter [data-f]')].map((b) => [b.dataset.f, Number(b.querySelector('em').textContent)])")
        self.assertEqual([k for k, _ in counts], ["all", "new", "known"])
        self.assertEqual(counts[0][1], len(rows))
        self.assertEqual(counts[1][1] + counts[2][1], len(rows))
        for key, n in counts:
            self.ev(f"() => document.querySelector('.mm-rec-filter [data-f={key}]').click()")
            self.assertEqual(self.ev("() => document.querySelectorAll('.mm-rec-list .mm-orow').length"), n)
        self.ev("() => document.querySelector('.mm-rec-filter [data-f=all]').click()")

        first = rows[0]
        self.ev("() => document.querySelector('.mm-rec-list [data-hide]').click()")
        self.wait(f"() => MemuMusic.S.prefs.recommendations.length === {len(rows) - 1}")
        self.assertEqual(len(self.ev("() => MemuMusic.S.prefs.recsHidden")), 1)
        self.assertNotIn(first["title"], self.ev("() => MemuMusic.S.prefs.recommendations.map((r) => r.title)"))
        self.wait("() => !!document.querySelector('[data-rec-unhide]')")

        artist = self.ev("() => MemuMusic.S.prefs.recommendations[0].artist")
        self.ev("() => document.querySelector('.mm-rec-list [data-more]').click()")
        self.wait("() => [...document.querySelectorAll('button, [role=menuitem]')].some((b) => /Не предлагать исполнителя/.test(b.textContent))")
        self.ev("() => [...document.querySelectorAll('button, [role=menuitem]')].find((b) => /Не предлагать исполнителя/.test(b.textContent)).click()")
        self.wait(f"() => !MemuMusic.S.prefs.recommendations.some((r) => r.artist === {artist!r})")
        self.assertEqual(len(self.ev("() => MemuMusic.S.prefs.recsHiddenArtists")), 1)
        self.ev("() => document.querySelector('[data-rec-unhide]').click()")
        self.assertEqual(self.ev("() => [MemuMusic.S.prefs.recsHidden.length, MemuMusic.S.prefs.recsHiddenArtists.length]"), [0, 0])

        left = self.ev("() => MemuMusic.S.prefs.recommendations.length")
        self.ev("() => document.querySelector('[data-rec-play]').click()")
        self.wait("() => !!(MemuMusic.Player.current() && MemuMusic.Player.current().online_url)")
        self.assertTrue(self.ev("() => MemuMusic.Player.current().id.startsWith('online:')"))
        self.assertEqual(self.ev("() => MemuMusic.Player.st.queue.length"), left)

    def test_stop_keeps_what_was_found(self):
        self.ev("() => { MemuMusic.S.prefs.recs = {depth: 32, perArtist: 0, noLibrary: false}; MemuMusic.go('recommendations', {}, {instant: true}); }")
        self.wait("() => !!document.querySelector('[data-rec-run]')")
        self.ev("() => { window.__slowRelated = 700; document.querySelector('[data-rec-run]').click(); }")
        self.wait("() => /найдено [1-9]/.test(document.querySelector('[data-rec-status]').textContent)", 30000)
        self.ev("() => document.querySelector('[data-rec-cancel]').click()")
        self.wait("() => document.querySelector('[data-rec-cancel]').hidden && !document.querySelector('[data-rec-run]').disabled", 5000)
        meta = self.ev("() => MemuMusic.S.prefs.recsMeta")
        self.assertTrue(meta["partial"])
        self.assertGreater(self.ev("() => MemuMusic.S.prefs.recommendations.length"), 0)
        self.assertIn("остановлен", self.ev("() => document.querySelector('[data-rec-status]').textContent"))

    def test_streamed_track_lyrics_are_requested_with_its_title(self):
        self.ev("() => { MemuMusic.S.prefs.onlineSource = 'ytmusic'; MemuMusic.go('search', { q: 'Glass Rivers' }, { instant: true }); }")
        self.page.wait_for_timeout(300)
        self.ev("() => document.querySelector('#gs-online').click()")
        self.wait("() => document.querySelectorAll('.mm-orow [data-dl]').length > 1")
        # «Listen» from the row menu of a result that is not in the library: it is streamed, not downloaded.
        self.ev("() => { const rows = [...document.querySelectorAll('.mm-orow')].filter((r) => !r.querySelector('.mm-oinlib')); rows[0].querySelector('[data-more]').click(); }")
        self.wait("() => !document.querySelector('#menu').hidden")
        self.ev("() => document.querySelector('#menu button[data-i=\"0\"]').click()")
        self.wait("() => !!(MemuMusic.Player.current() && MemuMusic.Player.current().online_url)")
        self.ev("() => MemuMusic.NP().open('lyrics')")
        self.wait("() => document.querySelectorAll('#lyrics-inner .mm-lyric').length > 3")
        call = self.ev("() => window.__ops.filter(([op]) => op === 'lyrics.get').pop()[1]")
        current = self.ev("() => MemuMusic.Player.current()")
        self.assertEqual(call["id"], current["id"])
        self.assertTrue(call["id"].startswith("online:"))
        self.assertEqual((call["title"], call["artist"], call["url"]), (current["title"], current["artist"], current["online_url"]))
        self.assertGreater(call["duration"], 0)
        self.assertFalse(self.ev("() => window.__ops.some(([op]) => op === 'download.start')"))


if __name__ == "__main__":
    unittest.main()
