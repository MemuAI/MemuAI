"""Round 39 (r39-integ): MemuMusic in Quick Access and the MemuAI window.

- player waits (SSE, long-poll) do not hold worker threads; a command reaches the playing side at once;
- next / prev resolved on the server (a one-track queue continues with the library, prev past 3 s → start);
- «music_play» / «music.play» tool names become module_command;
- widget settings: dock mode, chat mini player off by default, command cards;
- live (Chromium + playground): Quick Access buttons answer at once (optimistic), the dock pauses quickly, next changes
  the track, the ring fades out on pause, the dock / mini player follow their settings without a reload.
Screenshots: R39_SHOTS=<dir>.
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
SHOTS = os.environ.get("R39_SHOTS", "")

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

class ServerSideTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.old = os.environ.get("MEMUAI_DATA_DIR")
        os.environ["MEMUAI_DATA_DIR"] = cls.tmp.name
        from app.music import library
        if hasattr(library, "_LIBS"):
            library._LIBS.clear()
        cls.lib = library.get_library()
        folder = Path(cls.tmp.name) / "f"
        folder.mkdir()
        cls.ids = []
        for title, artist in (("Believer", "Imagine Dragons"), ("Numb", "Linkin Park"), ("Halo", "Beyonce")):
            path = folder / f"{title}.opus"
            path.write_bytes(b"0")
            cls.ids.append(cls.lib.add({"file": str(path), "title": title, "artist": artist, "duration": 180})["id"])
        cls.root = Path(cls.tmp.name) / "modules"
        from app import modules_host as mh
        mh.configure_module(cls.root, "music", enabled=True)

    @classmethod
    def tearDownClass(cls):
        if cls.old is None:
            os.environ.pop("MEMUAI_DATA_DIR", None)
        else:
            os.environ["MEMUAI_DATA_DIR"] = cls.old
        cls.tmp.cleanup()

    def run_cmd(self, command, args=None):
        from app import module_integration as mi
        return asyncio.run(mi.run_command("music", command, args or {}, root=self.root))

    def hub(self):
        from app.music.player import hub
        return hub(self.lib.root)

    def test_wait_async_wakes_without_threads(self):
        h = self.hub()

        async def go():
            task = asyncio.create_task(h.wait_async(h.version, h.cmd_seq, 5))
            await asyncio.sleep(0.05)
            t0 = time.perf_counter()
            await asyncio.to_thread(h.command, "pause", {"target": "pc"})
            out = await task
            return time.perf_counter() - t0, out
        took, out = asyncio.run(go())
        self.assertLess(took, 0.2)
        self.assertEqual([c["cmd"] for c in out["commands"]], ["pause"])
        self.assertFalse(h._waiters)
        api = (ROOT / "app/music/api.py").read_text(encoding="utf-8")
        self.assertNotIn("to_thread(player.wait", api)

    def test_next_prev_resolved_on_server(self):
        h = self.hub()
        h.update({"track_id": self.ids[1], "queue": [self.ids[1]], "index": 0, "playing": True, "position": 1,
                  "device": "pc", "device_name": "MemuAI", "duration": 180})
        out = self.run_cmd("next")
        self.assertTrue(out["ok"], out)
        self.assertNotEqual(out["card"]["id"], self.ids[1])
        self.assertEqual(h.commands[-1]["cmd"], "play_track")
        self.assertEqual(h.commands[-1]["args"]["track_id"], out["card"]["id"])
        h.update({"track_id": self.ids[1], "queue": [self.ids[1]], "index": 0, "playing": True, "position": 12, "device": "pc", "device_name": "MemuAI"})
        self.run_cmd("prev")
        self.assertEqual(h.commands[-1]["cmd"], "seek")
        h.update({"track_id": self.ids[1], "queue": [self.ids[0], self.ids[1]], "index": 1, "playing": True, "position": 1, "device": "pc", "device_name": "MemuAI"})
        out = self.run_cmd("prev")
        self.assertEqual(out["card"]["id"], self.ids[0])
        pause = self.run_cmd("pause")
        self.assertFalse(pause["card"]["playing"])

    def test_alias_and_widgets(self):
        from app import module_integration as mi
        self.assertEqual(mi.alias_tool("music_play", {"query": "x"}, root=self.root),
                         {"tool": "module_command", "args": {"module": "music", "command": "play", "args": {"query": "x"}}})
        self.assertEqual(mi.alias_tool("music.next", {}, root=self.root)["args"]["command"], "next")
        self.assertIsNone(mi.alias_tool("web_search", {}, root=self.root))
        widgets = {w["id"]: w for w in mi.public(self.root)["modules"][0]["widgets"]}
        self.assertEqual(widgets["dock"]["slot"], "main.dock")
        self.assertEqual(widgets["dock"]["values"], {"mode": "always"})
        self.assertFalse(widgets["chat-mini"]["values"]["enabled"])
        self.assertTrue(widgets["chat-cards"]["values"]["enabled"])
        self.assertEqual(mi.save_item_settings("music", "widget", "dock", {"mode": "off"}, root=self.root), {"mode": "off"})

    def test_static(self):
        quick = (ROOT / "app/pgstatic/quick/plugins/modules.js").read_text(encoding="utf-8")
        self.assertNotIn("setInterval", quick)
        for needle in ("memu-qmod-catalogue", "skeleton", "qmod-seek", "data-np-vol", "library.like", 'route: "np"', "stopRing(true)"):
            self.assertIn(needle, quick)
        dock = (ROOT / "app/pgstatic/music-dock.js").read_text(encoding="utf-8")
        self.assertIn("libraryContinue", dock)
        self.assertIn("dockMode", dock)
        self.assertIn('"/api/pg/modint/settings"', (ROOT / "app/pgstatic/module-integration.js").read_text(encoding="utf-8"))
        self.assertIn("aliasCall", (ROOT / "app/pgstatic/app.js").read_text(encoding="utf-8"))
        self.assertIn("alias_tool", (ROOT / "app/quick_ask_api.py").read_text(encoding="utf-8"))

@unittest.skipUnless(sync_playwright and CHROMIUM and shutil.which("ffmpeg"), "needs playwright, Chromium and ffmpeg")
class LiveQuickTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import httpx
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        tones = root / "tones"
        tones.mkdir()
        for i, freq in enumerate((330, 440)):
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=90",
                            "-c:a", "libopus", "-b:a", "32k", str(tones / f"tone{i}.opus")], check=True)
        cls.port = _free_port()
        env = dict(os.environ, MEMUAI_DATA_DIR=str(root / "data"), MEMUAI_PROXY_URL="http://127.0.0.1:9", NO_PROXY="127.0.0.1,localhost")
        subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); from app import pgdata; "
                        "pgdata.save_preferences({'profile_onboarded': True, 'profile_name': 'Test'})" % str(ROOT)], env=env, check=True)
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
        tracks = sorted(cls.call("library.list", {"sort": "title"})["tracks"], key=lambda t: t["title"])
        for track, (title, artist, accent) in zip(tracks, [("Golden Hour", "Kai Lumen", "#d9a441"), ("Night Drive", "Neon Avenue", "#8b6cf0")]):
            cls.call("library.update", {"id": track["id"], "title": title, "artist": artist, "accent": accent})
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"])
        cls.main = cls.browser.new_page(viewport={"width": 1280, "height": 800})
        cls.errors = []
        cls.main.on("pageerror", lambda exc: cls.errors.append(str(exc)))
        cls.main.goto(cls.base + "/")
        cls.poll(cls.main, "() => !!(window.MemuModInt && MemuModInt.state().modules >= 1 && window.MemuMusicDock)", 60000)

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
    def poll(target, script, timeout=20000):
        for _ in range(timeout // 50):
            try:
                if target.evaluate(script):
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.05)
        raise TimeoutError(script)

    def shot(self, page, name):
        if SHOTS:
            Path(SHOTS).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(SHOTS) / name))

    def test_quick_player_latency_skip_ring_and_settings(self):
        main = self.main
        quick = self.browser.new_page(viewport={"width": 760, "height": 560})
        errors = []
        quick.on("pageerror", lambda exc: errors.append(str(exc)))
        quick.goto(self.base + "/quick/")
        self.poll(quick, "() => window.MemuQuickModules && MemuQuickModules.state().ready && MemuQuickModules.state().phrases > 5", 30000)

        quick.fill("#qa-input", "включи трек golden hour")
        quick.press("#qa-input", "Enter")
        quick.wait_for_selector(".qmod-card-player [data-np-root]", timeout=15000)
        main.evaluate("""async () => { const r = await fetch('/api/pg/modint/command', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ module: 'music', command: 'play', args: { query: 'golden hour' } }) }).then((x) => x.json()); await MemuModInt.client(r.client); }""")
        self.poll(main, "() => MemuMusicDock.state().booted && !MemuMusicDock.state().paused", 20000)
        self.poll(quick, "() => MemuQuickModules.state().playing && MemuQuickModules.state().ring && !MemuQuickModules.state().expect", 15000)
        self.assertIn("Golden Hour", quick.inner_text(".qmod-card-player"))
        time.sleep(1.2)
        self.shot(quick, "01-quick-player.png")

        t0 = time.perf_counter()
        t_visual, visual = quick.evaluate("""() => { const t = performance.now(); document.querySelector('.qmod-card-player [data-np="toggle"]').click();
            const ok = !MemuQuickModules.state().playing && !document.querySelector('[data-np-root]').classList.contains('is-playing');
            return [(performance.now() - t) / 1000, ok]; }""")
        self.assertTrue(visual)
        self.poll(main, "() => MemuMusicDock.state().paused", 5000)
        t_sound = time.perf_counter() - t0
        print(f"\n[r39-integ] Quick Access pause: visual {t_visual * 1000:.1f} ms, dock paused after {t_sound * 1000:.0f} ms")
        self.assertLess(t_visual, 0.15, t_visual)
        self.assertLess(t_sound, 0.6, t_sound)
        self.assertTrue(quick.evaluate("() => MemuQuickModules.state().fading || !MemuQuickModules.state().ring"))
        self.poll(quick, "() => !MemuQuickModules.state().ring", 2000)
        self.shot(quick, "02-quick-paused.png")

        quick.click('.qmod-card-player [data-np="toggle"]')
        self.poll(main, "() => !MemuMusicDock.state().paused", 8000)
        before = main.evaluate("() => MemuMusicDock.state().trackId")
        quick.click('.qmod-card-player [data-np="next"]')
        self.poll(main, "(id) => MemuMusicDock.state().trackId !== '%s' && !MemuMusicDock.state().paused" % before, 8000)
        self.poll(quick, "() => document.querySelector('.qmod-card-player .qmod-np-title').textContent === 'Night Drive'", 8000)

        time.sleep(3.5)
        quick.click('.qmod-card-player [data-np="prev"]')
        self.poll(main, "() => MemuMusicDock.state().position < 2", 5000)
        self.assertEqual(main.evaluate("() => MemuMusicDock.state().track.title"), "Night Drive")

        quick.evaluate("() => { const s = document.querySelector('[data-np-seek]'); const r = s.getBoundingClientRect(); "
                       "s.dispatchEvent(new PointerEvent('pointerdown', { clientX: r.left + r.width * 0.5, bubbles: true })); }")
        self.poll(main, "() => MemuMusicDock.state().position > 30", 5000)

        self.poll(main, "() => !document.getElementById('memu-music-dock').hidden", 5000)
        self.assertFalse(main.evaluate("() => !!document.querySelector('.mmi-chat-np:not([hidden])')"))
        self.http.post(self.base + "/api/pg/modint/settings", json={"module": "music", "kind": "widget", "id": "dock", "values": {"mode": "off"}})
        main.evaluate("() => MemuModInt.client({ modint_reload: true })")
        self.poll(main, "() => document.getElementById('memu-music-dock').hidden", 5000)
        self.http.post(self.base + "/api/pg/modint/settings", json={"module": "music", "kind": "widget", "id": "chat-mini", "values": {"enabled": True}})
        main.evaluate("() => MemuModInt.client({ modint_reload: true })")
        main.wait_for_selector(".mmi-chat-np:not([hidden])", timeout=5000)
        self.shot(main, "03-main-mini-only.png")
        self.http.post(self.base + "/api/pg/modint/settings", json={"module": "music", "kind": "widget", "id": "dock", "values": {"mode": "always"}})
        self.http.post(self.base + "/api/pg/modint/settings", json={"module": "music", "kind": "widget", "id": "chat-mini", "values": {"enabled": False}})
        main.evaluate("() => MemuModInt.client({ modint_reload: true })")
        self.poll(main, "() => !document.getElementById('memu-music-dock').hidden && !document.querySelector('.mmi-chat-np:not([hidden])')", 5000)
        main.evaluate("() => MemuMusicDock.toggle()")
        self.assertEqual(errors, [])
        self.assertEqual([e for e in self.errors if "ResizeObserver" not in e], [])
        quick.close()

    def test_settings_page_widgets(self):
        main = self.main
        main.evaluate("() => { const host = document.createElement('div'); host.id = 'r39-wset'; document.body.prepend(host); return MemuModInt.renderWidgetSettings(host); }")
        main.wait_for_selector("#r39-wset .mmi-wset-row[data-mmi-row=dock] button[data-v=playing]", timeout=8000)
        main.click("#r39-wset .mmi-wset-row[data-mmi-row=dock] button[data-v=playing]")
        self.poll(main, "() => (MemuModInt.widgetValues('main.dock') || {}).mode === 'playing'", 5000)
        main.evaluate("() => document.getElementById('r39-wset').scrollIntoView()")
        self.shot(main, "04-settings-widgets.png")
        main.click("#r39-wset .mmi-wset-row[data-mmi-row=dock] button[data-v=always]")
        self.poll(main, "() => (MemuModInt.widgetValues('main.dock') || {}).mode === 'always'", 5000)
        main.evaluate("() => document.getElementById('r39-wset').remove()")

@unittest.skipUnless(sync_playwright and CHROMIUM and shutil.which("ffmpeg"), "needs playwright, Chromium and ffmpeg")
class MusicQueueLiveTest(LiveQuickTest):
    """The MemuMusic window itself (real server, real audio): next / prev / the end of a track move through the queue, and a
    one-track queue (a track started from Quick Access, handed over, restored) continues with the library instead of
    restarting the same song or stopping."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        short = Path(cls.tmp.name) / "short"
        short.mkdir()
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=660:duration=4",
                        "-c:a", "libopus", "-b:a", "32k", str(short / "short.opus")], check=True)
        cls.call("import.folder", {"path": str(short)})
        for _ in range(60):
            if (cls.call("library.list", {}) or {}).get("total") == 3:
                break
            time.sleep(0.5)
        cls.main.evaluate("() => MemuModules.open('music')")
        cls.frame = None
        for _ in range(200):
            cls.frame = next((f for f in cls.main.frames if f != cls.main.main_frame and "/modules/music/" in f.url), None)
            try:
                if cls.frame and cls.frame.evaluate("() => !!(window.MemuMusic && MemuMusic.S.loaded && MemuMusic.S.tracks.length === 3)"):
                    break
            except Exception:  # noqa: BLE001
                pass
            cls.main.wait_for_timeout(200)

    def test_quick_player_latency_skip_ring_and_settings(self):
        self.skipTest("covered by LiveQuickTest")

    def test_settings_page_widgets(self):
        self.skipTest("covered by LiveQuickTest")

    def cur(self):
        return self.frame.evaluate("() => { const P = MemuMusic.Player, c = P.current(); return { id: c && c.id, index: P.st.index, n: P.st.order.length, playing: P.st.playing, t: P.times().cur }; }")

    def test_next_prev_ended_and_one_track_queue(self):
        f = self.frame
        self.assertIsNotNone(f)
        ids = f.evaluate("() => MemuMusic.S.tracks.map((x) => x.id)")

        f.evaluate("() => { const S = MemuMusic.S; MemuMusic.Player.playList(S.tracks.filter((x) => x.duration > 10), null, 'T'); }")
        self.poll(f, "() => MemuMusic.Player.st.playing", 15000)
        first = self.cur()["id"]
        f.click("#next")
        self.poll(f, "(id) => MemuMusic.Player.current().id !== '%s' && MemuMusic.Player.st.playing" % first, 8000)
        f.click("#prev")
        self.poll(f, "() => MemuMusic.Player.current().id === '%s'" % first, 8000)

        long_id = [i for i in ids if f.evaluate("(id) => MemuMusic.S.byId.get(id).duration > 10", i)][0]
        f.evaluate("(id) => MemuMusic.Player.playList([MemuMusic.S.byId.get(id)], id, 'One')", long_id)
        self.poll(f, "() => MemuMusic.Player.st.playing", 8000)
        f.evaluate("() => MemuMusic.Player.next()")
        self.poll(f, "(id) => MemuMusic.Player.current().id !== '%s' && MemuMusic.Player.st.order.length > 1" % long_id, 8000)

        f.evaluate("() => MemuMusic.Player.prev()")
        self.poll(f, "() => MemuMusic.Player.current().id === '%s'" % long_id, 8000)

        short_id = [i for i in ids if f.evaluate("(id) => MemuMusic.S.byId.get(id).duration < 10", i)][0]
        f.evaluate("(id) => MemuMusic.Player.playList([MemuMusic.S.byId.get(id)], id, 'Short')", short_id)
        self.poll(f, "() => MemuMusic.Player.st.playing", 8000)
        self.poll(f, "(id) => MemuMusic.Player.current().id !== '%s' && MemuMusic.Player.st.playing" % short_id, 15000)

        self.poll(f, "() => MemuMusic.Player.times().cur > 3.3", 8000)
        now = self.cur()["id"]
        f.evaluate("() => MemuMusic.Player.prev()")
        self.poll(f, "() => MemuMusic.Player.times().cur < 2", 5000)
        self.assertEqual(self.cur()["id"], now)
        f.evaluate("() => MemuMusic.Player.pause()")

if __name__ == "__main__":
    unittest.main()
