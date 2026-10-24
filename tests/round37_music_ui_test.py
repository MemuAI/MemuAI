# -*- coding: utf-8 -*-
"""Round 37 — MemuMusic module front-end: static checks + headless Chromium in demo mode (?mock=1, no network)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
MODULE = ROOT / "app" / "module_builtins" / "music"

import round37_music_harness as harness  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None

PARTS = ("art.js", "api.js", "mock.js", "player.js", "fx.js", "views.js", "np.js")


def package():
    from app.modules_host import load_package
    with tempfile.TemporaryDirectory() as temp:
        return load_package(Path(temp), "music")


class MusicStaticTest(unittest.TestCase):
    def test_manifest_contract(self):
        m = json.loads((MODULE / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(m["id"], "music")
        self.assertEqual(m["title"], "MemuMusic")
        self.assertTrue(m["enabled"])
        # Round 39: the drawn icon file (app/module_icons.py); older code still gets the glyph «music».
        self.assertEqual(m["icon"], "icon.svg")
        self.assertTrue((ROOT / "app" / "module_builtins" / "music" / "icon.svg").is_file())
        self.assertEqual((m["window"]["default_width"], m["window"]["default_height"]), (1280, 820))
        self.assertEqual((m["window"]["min_width"], m["window"]["min_height"]), (720, 480))
        self.assertIn("music", m["permissions"])
        self.assertLessEqual(set(m["persist"]), {"player", "prefs", "recent_searches"})

    def test_parts_are_inlined_into_one_script(self):
        pkg = package()
        js = pkg["js"]
        for name in PARTS:
            self.assertIn(f"/* @memu-inlined-part:{name} */", js, name)
        self.assertNotIn("/* @memu-part:", js)
        self.assertLess(len(js), 1_000_000)
        node = shutil.which("node")
        if node:
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
                fh.write("(function(){\n" + js + "\n});")
            subprocess.run([node, "--check", fh.name], check=True)

    def test_every_js_file_parses(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node missing")
        for path in MODULE.glob("*.js"):
            subprocess.run([node, "--check", str(path)], check=True)

    def test_i18n_both_languages_and_every_key_exists(self):
        data = json.loads((MODULE / "i18n.json").read_text(encoding="utf-8"))
        self.assertEqual(set(data["ru"]), set(data["en"]))
        used = set()
        for path in MODULE.glob("*.js"):
            text = path.read_text(encoding="utf-8")
            used |= set(re.findall(r'\bt\("([a-z0-9_.]+)"', text))
            used |= set(re.findall(r'plural\("([a-z0-9_.]+)"', text))
        used |= set(re.findall(r'data-i18n[a-z-]*="([^"]+)"', (MODULE / "index.html").read_text(encoding="utf-8")))
        used = {k for k in used if not k.endswith(".")}
        self.assertEqual(sorted(used - set(data["ru"])), [])
        dynamic = ["repeat.off", "repeat.all", "repeat.one", "quality.best", "quality.normal", "quality.small",
                   "greet.morning", "greet.day", "greet.evening", "greet.night", "job.queued", "job.running", "job.done",
                   "kind.track", "kind.album", "kind.playlist", "kind.artist"] + [f"eq.preset.{k}" for k in
                   ("flat", "bass", "vocal", "electronic", "rock", "acoustic", "night", "custom")]
        for key in dynamic:
            self.assertIn(key, data["ru"]); self.assertIn(key, data["en"])

    def test_performance_rules(self):
        css = (MODULE / "style.css").read_text(encoding="utf-8")
        js = "\n".join(p.read_text(encoding="utf-8") for p in MODULE.glob("*.js"))
        self.assertNotRegex(css, r"backdrop-filter\s*:")   # nothing blurs a scrolling list
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn("html[data-calm]", css)
        self.assertNotIn("setInterval", js)                  # timers only as one-shot chains
        self.assertNotIn("MutationObserver", js)
        self.assertIn("document.hidden", js)
        self.assertIn('{ passive: true }', js)
        self.assertRegex(js, r"mock=1")                      # the mock is opt-in or a fallback only

    def test_mock_only_when_bridge_is_missing(self):
        api = (MODULE / "api.js").read_text(encoding="utf-8")
        self.assertIn("typeof m.call === \"function\"", api)
        self.assertIn("|| !bridge()", api)


@unittest.skipUnless(sync_playwright and harness.chromium(), "playwright + chromium required")
class MusicBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser, cls.page, cls.errors = harness.open_page(cls.pw, "ru", "dark")
        cls.frame = harness.frame_of(cls.page)
        harness.wait(cls.page, cls.frame, "() => !!(window.MemuMusic && MemuMusic.S.loaded)")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def ev(self, script, arg=None):
        return self.frame.evaluate(script, arg)

    def wait(self, script, timeout=8000):
        return harness.wait(self.page, self.frame, script, timeout)

    def test_1_home_greeting_carousel_mixes(self):
        self.ev("() => MemuMusic.go('home', {}, {force: true, instant: true})")
        self.wait("() => !!document.querySelector('.mm-flow-item')")
        self.assertTrue(self.ev("() => document.querySelector('.mm-greet').textContent.length > 3"))
        self.assertGreater(self.ev("() => document.querySelectorAll('.mm-flow-item').length"), 3)
        self.assertGreater(self.ev("() => document.querySelectorAll('.mm-mix').length"), 1)
        self.assertTrue(self.ev("() => document.querySelector('#flow-title').textContent.length > 0"))

    def test_2_every_section_renders(self):
        for view in ("library", "playlists", "download", "stats", "settings", "liked", "home"):
            self.ev(f"() => MemuMusic.go('{view}', {{}}, {{instant: true}})")
            self.page.wait_for_timeout(300)
            self.assertEqual(self.ev("() => document.querySelector('#app').dataset.view"), view)
            self.assertFalse(self.ev("() => !!document.querySelector('.mm-view .mm-empty .ill[viewBox] ~ b') && document.querySelector('.mm-view').textContent.includes('Что-то пошло не так')"), view)
        self.assertEqual([e for e in self.errors if "favicon" not in e], [])

    def test_3_library_list_is_virtual(self):
        self.ev("""() => { const S = MemuMusic.S; window.__realTracks = S.tracks; const base = S.tracks.slice(0, 5); const many = [];
            for (let i = 0; i < 5000; i++) many.push(Object.assign({}, base[i % 5], { id: 'v' + i, title: 'Track ' + i }));
            MemuMusic.setTracks(many); S.prefs.lib = { tab: 'tracks', sort: 'added', filter: 'all' }; MemuMusic.go('library', {}, {force: true, instant: true}); }""")
        self.wait("() => document.querySelectorAll('.mm-row').length > 0")
        rows = self.ev("() => document.querySelectorAll('.mm-row').length")
        self.assertLess(rows, 120)
        self.ev("() => { const m = document.querySelector('#main'); m.scrollTop = 56 * 3000; m.dispatchEvent(new Event('scroll')); }")
        self.wait("() => [...document.querySelectorAll('.mm-row')].some((r) => +r.dataset.i > 2900)")
        self.ev("() => { const q = document.querySelector('#lib-q'); q.value = 'Track 4999'; q.dispatchEvent(new Event('input')); }")
        self.wait("() => document.querySelectorAll('.mm-row').length === 1")
        self.ev("() => MemuMusic.setTracks(window.__realTracks)")   # later tests play real demo tracks
        self.ev("() => MemuMusic.rerender()")

    def test_4_link_detection_is_instant(self):
        self.ev("() => MemuMusic.go('download', {url: 'https://music.youtube.com/playlist?list=PL123'}, {force: true, instant: true})")
        self.wait("() => !!document.querySelector('#dl-logo.on')")
        self.assertIn("YouTube Music", self.ev("() => document.querySelector('#dl-detect').textContent"))
        cases = {"https://open.spotify.com/track/abc": ("spotify", "track"), "https://music.apple.com/ru/album/x/123": ("apple", "album"),
                 "https://soundcloud.com/a/sets/b": ("soundcloud", "playlist"), "https://music.yandex.ru/album/1/track/2": ("yandex", "track"),
                 "https://youtu.be/dQw4w9WgXcQ": ("youtube", "track"), "привет": None}
        for url, want in cases.items():
            got = self.ev("(u) => { const d = MemuMusic.detectLocal(u); return d ? [d.source, d.kind] : null; }", url)
            self.assertEqual(tuple(got) if got else None, want, url)

    def test_5_download_job_shows_progress(self):
        self.ev("() => MemuMusic.go('download', {}, {force: true, instant: true})")
        self.ev("() => { const i = document.querySelector('#dl-url'); i.value = 'https://www.youtube.com/watch?v=abc'; i.dispatchEvent(new Event('input')); document.querySelector('#dl-go').click(); }")
        self.wait("() => !!document.querySelector('.mm-job')")
        self.wait("() => !!document.querySelector('.mm-job[data-status=done]')", 15000)

    def test_6_play_now_playing_lyrics_and_keys(self):
        self.ev("() => { const S = MemuMusic.S; MemuMusic.Player.playList(S.tracks.slice(0, 5), S.tracks[0].id, 'T'); }")
        self.wait("() => MemuMusic.Player.st.playing", 15000)
        self.assertNotEqual(self.ev("() => document.querySelector('#bar-title').textContent"), "")
        self.ev("() => MemuMusic.NP().open('lyrics')")
        self.wait("() => !document.querySelector('#np').hidden && document.querySelectorAll('.mm-lyric, .mm-lyrics-msg, .mm-lyrics-empty').length > 0")
        self.ev("() => document.dispatchEvent(new KeyboardEvent('keydown', {code: 'Space', key: ' ', bubbles: true}))")
        self.wait("() => !MemuMusic.Player.st.playing")
        self.ev("() => document.dispatchEvent(new KeyboardEvent('keydown', {code: 'KeyM', key: 'm', bubbles: true}))")
        self.assertTrue(self.ev("() => MemuMusic.Player.st.muted"))
        self.ev("() => document.dispatchEvent(new KeyboardEvent('keydown', {code: 'KeyM', key: 'm', bubbles: true}))")
        self.ev("() => document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', code: 'Escape', bubbles: true}))")
        # Esc closes at once (isOpen() is false), the panel hides after its 340 ms exit animation — poll, a busy
        # headless run can be later than a fixed 500 ms
        self.assertFalse(self.ev("() => MemuMusic.NP().isOpen()"))
        self.wait("() => document.querySelector('#np').hidden", 4000)

    def test_7_queue_reorder_and_modes(self):
        self.ev("() => { const S = MemuMusic.S; MemuMusic.Player.playList(S.tracks.slice(0, 6), S.tracks[0].id, 'Q'); }")
        before = self.ev("() => MemuMusic.Player.upcoming().map((u) => u.track.id)")
        self.ev("() => MemuMusic.Player.moveInQueue(3, 1)")
        after = self.ev("() => MemuMusic.Player.upcoming().map((u) => u.track.id)")
        self.assertEqual(after[0], before[2])
        self.ev("() => MemuMusic.Player.cycleRepeat()")
        self.assertEqual(self.ev("() => MemuMusic.Player.st.repeat"), "all")
        self.ev("() => MemuMusic.Player.setEq([6,5,4,2,0,0,0,0,0,0], 'bass')")
        self.assertEqual(self.ev("() => MemuMusic.Player.st.eqPreset"), "bass")
        self.ev("() => MemuMusic.Player.pause()")

    def test_8_lite_effects_and_paste(self):
        # r38: «облегчённые эффекты» — в Настройки → Оформление → Анимация (theme.js), вкладки настроек — feat-settings.js
        self.ev("() => MemuMusic.go('settings', {tab: 'appearance'}, {force: true, instant: true})")
        self.wait("() => !!document.querySelector('[data-ui=motion] [data-v=lite]')")
        self.ev("() => document.querySelector('[data-ui=motion] [data-v=lite]').click()")
        self.assertTrue(self.ev("() => document.documentElement.hasAttribute('data-calm')"))
        self.ev("() => document.querySelector('[data-ui=motion] [data-v=full]').click()")
        self.assertFalse(self.ev("() => document.documentElement.hasAttribute('data-calm')"))
        self.ev("""() => { const dt = new DataTransfer(); dt.setData('text/plain', 'смотри https://soundcloud.com/artist/track'); 
            document.body.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true})); }""")
        self.wait("() => document.querySelector('#app').dataset.view === 'download' && !!document.querySelector('#dl-url') && document.querySelector('#dl-url').value.includes('soundcloud')")

    def test_10_lyrics_empty_state_and_paste(self):
        # m7 has no lyrics in the demo library: a calm empty screen with «Найти текст» / «Вставить текст», no endless skeletons
        self.ev("() => { const S = MemuMusic.S; MemuMusic.Player.playList([S.byId.get('m7')], 'm7', 'T'); MemuMusic.NP().open('lyrics'); }")
        self.wait("() => !!document.querySelector('.mm-lyrics-empty [data-ly=find]') && !document.querySelector('.mm-lyric-skel')")
        self.ev("() => document.querySelector('[data-ly=paste]').click()")
        self.wait("() => !!document.querySelector('.mm-lyrics-paste textarea')")
        self.ev("""() => { const a = document.querySelector('.mm-lyrics-paste textarea'); a.value = ['[00:01.00] One', '[00:03.50] Two', '[00:06] Three'].join(String.fromCharCode(10));
            document.querySelector('.mm-lyrics-paste [data-ly=save]').click(); }""")   # r40: a real button (a synthetic «submit» hid the dead sandboxed form)
        self.wait("() => document.querySelectorAll('.mm-lyric').length === 3")
        self.assertEqual(self.ev("() => [...document.querySelectorAll('.mm-lyric')].map((n) => Number(n.dataset.t))"), [1, 3.5, 6])
        self.ev("() => { MemuMusic.Player.pause(); MemuMusic.NP().close(); }")

    def test_11_generated_covers_vary(self):
        styles = self.ev("""() => { const seen = new Set(); for (let i = 0; i < 60; i++) { const svg = decodeURIComponent(MemuMusic.coverArt('k' + i, '', 'Title ' + i));
            seen.add((svg.match(/<(circle|path|rect|text|ellipse)/g) || []).slice(2, 6).join()); } return seen.size; }""")
        self.assertGreater(styles, 6)
        self.assertEqual(self.ev("() => MemuMusic.coverArt('same', '', 'X') === MemuMusic.coverArt('same', '', 'X')"), True)

    def test_12_download_retry_and_items(self):
        self.ev("() => MemuMusic.go('download', {}, {force: true, instant: true})")
        self.ev("() => MemuMusic.API.call('download.start', {url: 'https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy'}).then(() => MemuMusic.pollJobs())")
        self.wait("() => !!document.querySelector('.mm-job-items li')", 15000)
        self.wait("() => !!document.querySelector('[data-job-retry]')", 20000)
        self.ev("() => document.querySelector('[data-job-retry]').click()")
        self.wait("() => !document.querySelector('[data-job-retry]')", 15000)

    def test_13_stats_history(self):
        self.ev("() => MemuMusic.go('stats', {}, {force: true, instant: true})")
        self.wait("() => document.querySelectorAll('.mm-history-row').length > 3")

    def test_9_no_console_errors(self):
        self.assertEqual([e for e in self.errors if "favicon" not in e], [])


DOCK_JS = ROOT / "app" / "pgstatic" / "music-dock.js"
DOCK_CSS = ROOT / "app" / "pgstatic" / "music-dock.css"


class MusicDockStaticTest(unittest.TestCase):
    """The host player in the main window (app/pgstatic/music-dock.*) must cost nothing until music played."""

    def test_loaded_lazily_and_light(self):
        html = (ROOT / "app" / "pgstatic" / "index.html").read_text(encoding="utf-8")
        self.assertRegex(html, r'<script src="/music-dock\.js\?v=[^"]+" defer></script>')
        self.assertNotIn("music-dock.css", html)            # the stylesheet comes with the dock, not with every window
        js = DOCK_JS.read_text(encoding="utf-8")
        css = DOCK_CSS.read_text(encoding="utf-8")
        self.assertIn("music-dock.css", js)
        self.assertIn("requestIdleCallback", js)
        self.assertIn("musicEnabled()", js)
        self.assertNotIn("MutationObserver", js)
        self.assertEqual(js.count("setInterval("), 1)        # the expanded clock only, 1 s, visible page
        self.assertIn("setInterval(clock, 1000)", js)
        self.assertNotRegex(css, r"backdrop-filter\s*:")
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn("@keyframes mmd-progress", css)        # progress runs in CSS, no per-frame JS
        node = shutil.which("node")
        if node:
            subprocess.run([node, "--check", str(DOCK_JS)], check=True)

    def test_protocol(self):
        js = DOCK_JS.read_text(encoding="utf-8")
        for needle in ('"/api/pg/music/player/events"', 'device_name: "MemuAI"', 'device: "pc"', "handoff", "memu-modules-changed",
                       "mediaSession", 'by: "dock"', "at: Date.now()", "position_now"):
            self.assertIn(needle, js)
        main = (MODULE / "main.js").read_text(encoding="utf-8")
        self.assertIn("async function adopt()", main)
        self.assertIn('target: "dock"', main)
        self.assertIn('device_name: "MemuMusic"', main)


def _free_port() -> int:
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipUnless(sync_playwright and harness.chromium() and shutil.which("ffmpeg"), "playwright + chromium + ffmpeg required")
class MusicDockLiveTest(unittest.TestCase):
    """Real playground server + real main window: the module plays, its window closes, the dock carries on; reopening hands back."""

    @classmethod
    def setUpClass(cls):
        import os
        import time
        import httpx
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        tones = root / "tones"
        tones.mkdir()
        for i, freq in enumerate((330, 440)):
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=40",
                            "-c:a", "libopus", "-b:a", "48k", str(tones / f"t{i}.opus")], check=True)
        cls.port = _free_port()
        env = dict(os.environ, MEMUAI_DATA_DIR=str(root / "data"), MEMUAI_PROXY_URL="http://127.0.0.1:9", NO_PROXY="127.0.0.1,localhost")
        code = ("import sys, uvicorn; sys.path.insert(0, %r); from app.playground_api import create_app; "
                "uvicorn.run(create_app(), host='127.0.0.1', port=%d, log_level='error')") % (str(ROOT), cls.port)
        cls.proc = subprocess.Popen([sys.executable, "-c", code], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.http = httpx.Client(trust_env=False, timeout=30)
        for _ in range(240):
            try:
                if cls.http.get(cls.base + "/api/pg/modules").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        cls.http.post(cls.base + "/api/pg/modules/configure", json={"id": "music", "enabled": True})
        cls.call("import.folder", {"path": str(tones)})
        for _ in range(60):
            if (cls.call("library.list", {}) or {}).get("total") == 2:
                break
            time.sleep(0.5)
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=harness.chromium(), args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"])
        cls.page = cls.browser.new_page(viewport={"width": 1280, "height": 800})
        cls.errors = []
        cls.page.on("pageerror", lambda exc: cls.errors.append(str(exc)))
        cls.page.goto(cls.base + "/")
        cls.poll("() => !!(window.MemuModules && MemuModules.catalog.some((m) => m.id === 'music' && m.enabled) && window.MemuMusicDock)", 60000)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.proc.terminate()
        try:
            cls.proc.wait(5)
        except subprocess.TimeoutExpired:   # an open SSE stream keeps uvicorn waiting for the graceful shutdown
            cls.proc.kill()
            cls.proc.wait(5)
        cls.tmp.cleanup()

    @classmethod
    def call(cls, op, args):
        return cls.http.post(cls.base + "/api/pg/music/call", json={"op": op, "args": args}).json().get("result")

    @classmethod
    def poll(cls, script, timeout=20000, frame=None):
        for _ in range(timeout // 200):
            try:
                if (frame or cls.page).evaluate(script):
                    return True
            except Exception:
                pass
            cls.page.wait_for_timeout(200)
        raise TimeoutError(script)

    def module_frame(self):
        for _ in range(150):
            frame = next((f for f in self.page.frames if f != self.page.main_frame and "/modules/music/" in f.url), None)
            if frame:
                try:
                    if frame.evaluate("() => !!(window.MemuMusic && MemuMusic.S.loaded)"):
                        return frame
                except Exception:
                    pass
            self.page.wait_for_timeout(200)
        raise TimeoutError("music frame")

    def test_handoff_both_ways(self):
        self.page.evaluate("() => MemuModules.open('music')")
        frame = self.module_frame()
        frame.evaluate("() => { const S = MemuMusic.S; MemuMusic.Player.playList(S.tracks, S.tracks[0].id, 'T'); }")
        self.poll("() => MemuMusic.Player.st.playing && MemuMusic.Player.times().cur > 2", 20000, frame)
        self.assertTrue(frame.evaluate("() => !!MemuMusic.Player.analyser()"))      # CORS on stream → equalizer and visualizer
        at = frame.evaluate("() => MemuMusic.Player.times().cur")
        self.assertEqual(self.call("player.state", {})["device_name"], "MemuMusic")
        self.page.evaluate("() => MemuModules.requestClose('music')")
        self.poll("() => { const d = MemuMusicDock.state(); return d.mode === 'local' && !d.paused; }", 15000)
        self.assertGreaterEqual(self.page.evaluate("() => MemuMusicDock.state().position"), at - 1)
        self.assertFalse(self.page.evaluate("() => document.getElementById('memu-music-dock').hidden"))
        self.call("player.command", {"cmd": "pause"})                                  # the phone remote reaches the dock
        self.poll("() => MemuMusicDock.state().paused", 8000)
        self.call("player.command", {"cmd": "play"})
        self.poll("() => !MemuMusicDock.state().paused", 8000)
        self.page.evaluate("() => MemuModules.open('music')")
        frame = self.module_frame()
        self.poll("() => MemuMusic.Player.st.playing", 20000, frame)
        self.poll("() => MemuMusicDock.state().paused && MemuMusicDock.state().mode === 'remote'", 8000)
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
