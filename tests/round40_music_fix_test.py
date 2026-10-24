"""Round 40 (r40-music-fix): one player state, instantly everywhere; the lyrics «Save» buttons.

- server: a remote play / pause / toggle / stop changes the shared state at once (optimistic, versioned), the playing side
  confirms with its report; no confirmation in time → the state rolls back; the SSE answers without buffering;
- the module window reports play / pause / track changes at once (it used to debounce every report by 0.5 s);
- the module receives commands through its host page's SSE (no own long-poll connection while one is open);
- lyrics: «Paste lyrics → Save» works in the sandboxed module frame (a <form> submit is blocked there), the track editor
  saves lyrics alone (with a toast), the text survives a reload;
- live (Chromium + playground, several pages at once): the main window with the dock, Quick Access, and the module's own
  window (/?module_app=music) — sync latency in every direction: stop in the module → widgets, pause in a widget →
  module, next / prev, the end of a track.
Screenshots: R40_SHOTS=<dir>.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from playwright.sync_api import sync_playwright
except Exception:  # noqa: BLE001
    sync_playwright = None

CHROMIUM = next((c for c in (os.environ.get("MEMU_TEST_CHROMIUM", ""), "/workspace/work/chromium/chrome", shutil.which("chromium") or "")
                 if c and Path(c).exists()), "")
SHOTS = os.environ.get("R40_SHOTS", "")
LIMIT = float(os.environ.get("R40_SYNC_LIMIT_MS", "250")) / 1000   # <100 ms measured in the sandbox; headroom for slow CI


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class HubTest(unittest.TestCase):
    def hub(self):
        from app.music.player import PlayerHub
        h = PlayerHub(None)
        h.update({"track_id": "a" * 12, "queue": ["a" * 12, "b" * 12], "index": 0, "playing": True, "position": 10,
                  "device": "pc", "device_name": "MemuMusic", "duration": 200})
        return h

    def test_remote_pause_is_optimistic_and_confirmed(self):
        h = self.hub()
        v = h.version
        h.command("pause", {"target": "pc"})
        s = h.snapshot()
        self.assertFalse(s["playing"])               # every widget sees «paused» with the command itself
        self.assertTrue(s["pending"])
        self.assertGreater(s["version"], v)
        pos = s["position_now"]
        time.sleep(0.05)
        self.assertEqual(h.snapshot()["position_now"], pos)   # frozen, not extrapolated
        h.update({"playing": False, "position": pos, "device": "pc", "device_name": "MemuMusic"})
        self.assertFalse(h.snapshot()["pending"])

    def test_toggle_play_and_rollback(self):
        h = self.hub()
        h.command("toggle", {"target": "pc"})
        self.assertFalse(h.snapshot()["playing"])
        h.update({"playing": False, "device": "pc", "device_name": "MemuMusic"})
        h.command("play", {"target": "pc"})
        self.assertTrue(h.snapshot()["playing"])
        h.state["pending"]["until"] = time.time() - 1   # the player never confirmed → back to the reported state
        s = h.snapshot()
        self.assertFalse(s["playing"])
        self.assertFalse(s["pending"])

    def test_no_optimism_for_other_device_or_offline(self):
        h = self.hub()
        h.command("pause", {"target": "phone"})
        self.assertTrue(h.snapshot()["playing"])
        h.state["updated_at"] = time.time() - 500
        h.command("pause", {"target": "pc"})
        self.assertFalse(h.snapshot().get("pending"))
        h2 = self.hub()
        h2.command("next", {"target": "pc"})
        self.assertTrue(h2.snapshot()["playing"])

    def test_sse_headers_and_static(self):
        api = (ROOT / "app/music/api.py").read_text(encoding="utf-8")
        self.assertTrue("X-Accel-Buffering" in api and "retry: 1000" in api)
        main = (ROOT / "app/module_builtins/music/main.js").read_text(encoding="utf-8")
        self.assertFalse("timer = setTimeout(send, 500)" in main)
        np_js = (ROOT / "app/module_builtins/music/np.js").read_text(encoding="utf-8")
        self.assertFalse('type="submit"' in np_js or 'addEventListener("submit"' in np_js)   # sandboxed frame: no allow-forms


class LyricsRaceTest(unittest.TestCase):
    def test_late_search_keeps_the_users_lyrics(self):
        from app.music import library, lyrics
        with tempfile.TemporaryDirectory() as tmp:
            lib = library.Library(Path(tmp))
            self.addCleanup(lib.close)
            path = Path(tmp) / "a.opus"
            path.write_bytes(b"0")
            tid = lib.add({"file": str(path), "title": "Song", "artist": "X", "duration": 100})["id"]
            found = {"source": "lrclib", "synced": [[1.0, "web line"]], "plain": "web line", "score": 1}

            def slow_search(*_a, **_k):   # the user saves own lyrics while the web search is still running
                lyrics.set_manual(lib, tid, "[00:01.00]my line")
                return [found], {"lrclib": "ok"}
            old = lyrics.search
            lyrics.search = slow_search
            try:
                out = lyrics.fetch(lib, tid, force=True)
            finally:
                lyrics.search = old
            self.assertEqual(out["source"], "manual")
            self.assertEqual(lib.cached_lyrics(tid)["synced"], [[1.0, "my line"]])
            lib.close()  # close before TemporaryDirectory removes the SQLite file on Windows


@unittest.skipUnless(sync_playwright and CHROMIUM and shutil.which("ffmpeg"), "needs playwright, Chromium and ffmpeg")
class LiveSyncTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import httpx
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        tones = root / "tones"
        tones.mkdir()
        for i, (freq, dur) in enumerate(((330, 120), (440, 120), (550, 120))):
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={dur}",
                            "-c:a", "libopus", "-b:a", "32k", str(tones / f"tone{i}.opus")], check=True)
        cls.port = _free_port()
        env = dict(os.environ, MEMUAI_DATA_DIR=str(root / "data"), MEMUAI_PROXY_URL="http://127.0.0.1:9", NO_PROXY="127.0.0.1,localhost")
        subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); from app import pgdata; "
                        "pgdata.save_preferences({'profile_onboarded': True, 'profile_name': 'Test'})" % str(ROOT)], env=env, check=True)
        log = os.environ.get("R40_SERVER_LOG", "")   # an access log of the live server, for debugging the sync
        code = ("import sys, uvicorn; sys.path.insert(0, %r); from app.playground_api import create_app; "
                "uvicorn.run(create_app(), host='127.0.0.1', port=%d, log_level=%r)") % (str(ROOT), cls.port, "info" if log else "error")
        out = open(log, "w") if log else subprocess.DEVNULL
        cls.proc = subprocess.Popen([sys.executable, "-c", code], env=env, stdout=out, stderr=subprocess.STDOUT)
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
        cls.call("settings.set", {"auto_lyrics": False})   # no network: the import does not queue a lyrics search
        cls.call("import.folder", {"path": str(tones)})
        for _ in range(60):
            if (cls.call("library.list", {}) or {}).get("total") == 3:
                break
            time.sleep(0.5)
        tracks = sorted(cls.call("library.list", {"sort": "title"})["tracks"], key=lambda t: t["title"])
        for track, (title, artist) in zip(tracks, [("Alpha", "A"), ("Bravo", "B"), ("Charlie", "C")]):
            cls.call("library.update", {"id": track["id"], "title": title, "artist": artist})
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"])
        cls.ctx = cls.browser.new_context()
        cls.errors = []

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.proc.terminate()
        try:
            cls.proc.wait(5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
            cls.proc.wait(5)
        cls.tmp.cleanup()

    @classmethod
    def call(cls, op, args):
        return cls.http.post(cls.base + "/api/pg/music/call", json={"op": op, "args": args}).json().get("result")

    @staticmethod
    def poll(target, script, timeout=20000, arg=None):
        for _ in range(timeout // 40):
            try:
                if target.evaluate(script, arg):
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.04)
        raise TimeoutError(script)

    def page(self, url, viewport=(1280, 800)):
        page = self.ctx.new_page()
        page.set_viewport_size({"width": viewport[0], "height": viewport[1]})
        self.addCleanup(lambda: page.is_closed() or page.close())
        page.on("pageerror", lambda exc: self.errors.append(f"{url}: {exc}"))
        page.on("console", lambda m: m.type == "error" and self.errors.append(f"{url} console: {m.text}"))
        page.goto(self.base + url)
        return page

    def shot(self, page, name):
        if SHOTS:
            Path(SHOTS).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(SHOTS) / name))

    # in-page watchers: each page records Date.now() the moment its condition holds (one clock — one machine)
    ARM = """(cond) => { clearInterval(window.__r40w && window.__r40w.timer); const w = window.__r40w = { at: 0, cond };
        const f = new Function('return (' + cond + ')');
        w.timer = setInterval(() => { try { if (!w.at && f()) { w.at = Date.now(); clearInterval(w.timer); } } catch (e) {} }, 2); }"""

    def arm(self, target, cond):
        target.evaluate(self.ARM, cond)

    def landed(self, target, t0, timeout=8.0):
        end = time.time() + timeout
        while time.time() < end:
            at = target.evaluate("() => window.__r40w.at")
            if at:
                return (at - t0) / 1000
            time.sleep(0.02)
        return None

    def measure(self, name, act_target, act_js, watchers, results):
        for target, cond in watchers.values():
            self.arm(target, cond)
        t0 = act_target.evaluate("(js) => { const t = Date.now(); (new Function(js))(); return t; }", act_js)
        out = {}
        for label, (target, _cond) in watchers.items():
            out[label] = self.landed(target, t0)
        results[name] = out
        return out

    def open_module(self):
        app = self.page("/?module_app=music", (1100, 760))
        for _ in range(600):
            for f in app.frames[1:]:
                try:
                    if f.evaluate("() => !!(window.MemuMusic && MemuMusic.S.loaded && MemuMusic.S.tracks.length === 3)"):
                        return app, f
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(0.1)
        raise TimeoutError([f.url for f in app.frames])

    def test_lyrics_save_buttons(self):
        """«Paste lyrics → Save» in Now playing and «Save» of the track editor store the text (DB), show it, and it survives
        a reload of the window."""
        self.call("settings.set", {"auto_lyrics": False})      # no network: nothing is searched on open
        ids = {t["title"]: t["id"] for t in self.call("library.list", {})["tracks"]}
        self.call("lyrics.set", {"id": ids["Alpha"], "text": ""})
        self.call("lyrics.set", {"id": ids["Bravo"], "text": ""})
        app, f = self.open_module()
        f.evaluate("(id) => { MemuMusic.Player.playList([MemuMusic.S.byId.get(id)], id, 'L', { autoplay: false }); MemuMusic.NP().open('lyrics'); }", ids["Alpha"])
        f.wait_for_selector('#lyrics [data-ly="paste"]', timeout=10000)
        f.click('#lyrics [data-ly="paste"]')
        f.fill(".mm-lyrics-paste textarea", "[00:01.00]First line\n[00:03.50]Second line")
        self.shot(app, "lyrics-paste.png")
        f.click('#lyrics [data-ly="save"]')
        f.wait_for_selector("#lyrics-inner .mm-lyric", timeout=5000)
        self.poll(f, "() => [...document.querySelectorAll('.mm-toast')].some((n) => /сохран|saved/i.test(n.textContent))", 5000)
        got = self.call("lyrics.get", {"id": ids["Alpha"]})
        self.assertEqual([r[1] for r in got["synced"]], ["First line", "Second line"])
        self.assertEqual(got["source"], "manual")
        self.shot(app, "lyrics-saved.png")
        # Ctrl+Enter in the paste box saves too
        f.evaluate("(id) => MemuMusic.Player.playList([MemuMusic.S.byId.get(id)], id, 'L', { autoplay: false })", ids["Bravo"])
        f.wait_for_selector('#lyrics [data-ly="paste"]', timeout=10000)
        f.click('#lyrics [data-ly="paste"]')
        f.fill(".mm-lyrics-paste textarea", "Just words")
        f.press(".mm-lyrics-paste textarea", "Control+Enter")
        self.poll(f, "() => !!document.querySelector('#lyrics-inner .mm-lyrics-plain')", 5000)
        self.assertEqual(self.call("lyrics.get", {"id": ids["Bravo"]})["plain"], "Just words")
        # survives a reload of the window
        app.close()
        app, f = self.open_module()
        f.evaluate("(id) => { MemuMusic.Player.playList([MemuMusic.S.byId.get(id)], id, 'L', { autoplay: false }); MemuMusic.NP().open('lyrics'); }", ids["Alpha"])
        self.poll(f, "() => document.querySelectorAll('#lyrics-inner .mm-lyric').length === 2", 10000)
        self.assertIn("Second line", f.inner_text("#lyrics-inner"))
        f.evaluate("() => MemuMusic.NP().close()")
        # the track editor: lyrics alone are saved, with a toast
        f.evaluate("(id) => MemuMusic.dev.editTracks(MemuMusic.S.byId.get(id))", ids["Charlie"])
        f.click('.mm-ed-tabs [data-tab="lyrics"]')
        self.poll(f, "() => { const s = document.querySelector('#ed-ly-state'); return s && !/…|\\.\\.\\./.test(s.textContent); }", 8000)
        f.fill("#ed-lyrics", "[00:02.00]Edited in the editor")
        f.click("#ed-save")
        self.poll(f, "() => [...document.querySelectorAll('.mm-toast')].some((n) => /текст|lyrics/i.test(n.textContent))", 5000)
        got = self.call("lyrics.get", {"id": ids["Charlie"]})
        self.assertEqual([r[1] for r in got["synced"]], ["Edited in the editor"])
        errors = [e for e in self.errors if "ResizeObserver" not in e and "favicon" not in e and "503" not in e]
        self.assertEqual(errors, [])
        app.close()

    def test_multi_window_sync(self):
        main = self.page("/")
        self.poll(main, "() => !!(window.MemuModInt && MemuModInt.state().modules >= 1 && window.MemuMusicDock)", 60000)
        app = self.page("/?module_app=music", (1100, 760))
        frame = None
        for _ in range(600):
            for f in app.frames[1:]:
                try:
                    if f.evaluate("() => !!(window.MemuMusic && MemuMusic.S.loaded && MemuMusic.S.tracks.length === 3)"):
                        frame = f
                except Exception:  # noqa: BLE001
                    pass
            if frame:
                break
            time.sleep(0.1)
        self.assertIsNotNone(frame, [f.url for f in app.frames] + self.errors)
        quick = self.page("/quick/", (760, 560))
        self.poll(quick, "() => window.MemuQuickModules && MemuQuickModules.state().ready", 30000)
        # the module window plays a list
        frame.evaluate("() => { const S = MemuMusic.S; MemuMusic.Player.playList(S.tracks.slice().sort((a, b) => a.title.localeCompare(b.title)), null, 'T'); }")
        self.poll(frame, "() => MemuMusic.Player.st.playing", 15000)
        main.evaluate("() => MemuMusicDock.boot(true)")
        self.poll(main, "() => MemuMusicDock.state().booted && MemuMusicDock.state().playing && MemuMusicDock.state().trackId", 15000)
        self.poll(quick, "() => MemuQuickModules.state().playing", 15000)
        P = "MemuMusic.Player"
        dock_playing = "MemuMusicDock.state().playing"
        quick_playing = "MemuQuickModules.state().playing"
        res = {}
        time.sleep(0.6)
        # 1. stop (pause) in the module → dock + Quick Access
        self.measure("module pause → widgets", frame, P + ".pause()",
                     {"dock": (main, "!" + dock_playing), "quick": (quick, "!" + quick_playing)}, res)
        time.sleep(0.4)
        self.measure("module play → widgets", frame, P + ".play()",
                     {"dock": (main, dock_playing), "quick": (quick, quick_playing)}, res)
        time.sleep(0.6)
        # 2. pause in the dock widget → the module stops; the dock button flips at once and never flips back
        main.evaluate("""() => { window.__r40flips = []; const b = document.querySelector('#memu-music-dock .mmd-play');
            new MutationObserver(() => window.__r40flips.push(b.innerHTML.includes('rect') ? 'pause' : 'play')).observe(b, { childList: true }); }""")
        self.measure("dock pause → module", main, "MemuMusicDock.toggle()",
                     {"module": (frame, "!" + P + ".st.playing"), "quick": (quick, "!" + quick_playing),
                      "dock-button": (main, "!document.querySelector('#memu-music-dock').classList.contains('is-playing')")}, res)
        time.sleep(0.8)
        flips = main.evaluate("() => window.__r40flips")   # asserted at the end, after every latency is measured
        self.measure("dock play → module", main, "MemuMusicDock.toggle()",
                     {"module": (frame, P + ".st.playing"), "quick": (quick, quick_playing)}, res)
        time.sleep(0.6)
        # 3. Quick Access pause / play → module + dock
        self.measure("quick pause → module", quick, "MemuQuickModules.control('toggle')",
                     {"module": (frame, "!" + P + ".st.playing"), "dock": (main, "!" + dock_playing)}, res)
        time.sleep(0.6)
        self.measure("quick play → module", quick, "MemuQuickModules.control('toggle')",
                     {"module": (frame, P + ".st.playing"), "dock": (main, dock_playing)}, res)
        time.sleep(0.6)
        # 4. next / prev from the dock and in the module
        cur = frame.evaluate("() => MemuMusic.Player.current().id")
        self.measure("dock next → module + quick", main, "MemuMusicDock.control('next')",
                     {"module": (frame, f"MemuMusic.Player.current().id !== '{cur}'"),
                      "quick": (quick, f"MemuQuickModules.state().trackId !== '{cur}'"),
                      "dock": (main, f"MemuMusicDock.state().trackId !== '{cur}'")}, res)
        time.sleep(0.6)
        cur = frame.evaluate("() => MemuMusic.Player.current().id")
        self.poll(main, f"() => MemuMusicDock.state().trackId === '{cur}'", 5000)   # in sync before the next step
        self.measure("module prev → dock", frame, P + ".prev()",
                     {"dock": (main, f"MemuMusicDock.state().trackId !== '{cur}'")}, res)
        time.sleep(0.6)
        # 5. the end of a track: the next one starts by itself, widgets follow (measured from the module's own switch)
        cur = frame.evaluate("() => MemuMusic.Player.current().id")
        watch = {"dock": (main, f"MemuMusicDock.state().trackId !== '{cur}' && {dock_playing}"),
                 "quick": (quick, f"MemuQuickModules.state().trackId !== '{cur}'")}
        self.measure("track end (module switched)", frame, P + ".seek(" + P + ".times().dur - 0.6)",
                     dict(watch, module=(frame, f"MemuMusic.Player.current().id !== '{cur}'")), res)
        switched = res["track end (module switched)"].pop("module")
        res["track end → widgets"] = {k: (None if v is None or switched is None else max(0.0, v - switched))
                                      for k, v in res.pop("track end (module switched)").items()}
        time.sleep(0.4)
        # 6. stop from the module window's own dock-less page close: the module pauses, widgets stop
        self.measure("module stop → widgets", frame, P + ".pause()",
                     {"dock": (main, "!" + dock_playing), "quick": (quick, "!" + quick_playing)}, res)
        self.shot(main, "main.png")
        self.shot(app, "module-app.png")
        self.shot(quick, "quick.png")
        print("\n[r40-music-fix] sync latency (ms):")
        for name, row in res.items():
            print(f"  {name:28s} " + "  ".join(f"{k}={'—' if v is None else round(v * 1000)}" for k, v in row.items()))
        print(f"  dock button after one click: {flips}")
        self.assertEqual(set(flips), {"play"}, f"the dock button flickered back: {flips}")
        worst = max((v for row in res.values() for v in row.values() if v is not None), default=0)
        missing = [(n, k) for n, row in res.items() for k, v in row.items() if v is None or v < 0]   # < 0: out of sync before
        self.assertEqual(missing, [], res)
        self.assertLess(worst, LIMIT, res)
        errors = [e for e in self.errors if "ResizeObserver" not in e and "favicon" not in e and "503" not in e]
        self.assertEqual(errors, [])
        for p in (quick, app, main):
            p.close()


if __name__ == "__main__":
    unittest.main()
