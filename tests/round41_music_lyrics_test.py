"""Round 41 (r41-ui): MemuMusic lyrics — edit the existing text, and karaoke that keeps time with the sound.

- «Edit» above found / saved lyrics opens the editor with the current text (LRC with line and word stamps); Save stores
  it as «My lyrics», keeps the track's offset, and it is there after a reload of the window;
- the sync engine: Player.heard() (per frame, extrapolated between media clock updates, minus the WebAudio output
  latency), no fixed lead; measured in Chromium with real audio: the moment each line lights up against the audio clock
  (a least-squares fit of currentTime over the whole run, so its own quantization cancels out) — goal < 50 ms;
- the track offset: −/+ (Shift = 0.5 s), keys [ ] / Shift+[ ], «By line» (tap the line being sung → the offset);
  the general offset (Settings, lyrics_offset_ms) — and it is saved;
- the server: synced variants of the same length (±2 s) go first, «duration_diff» warns about another version,
  lyrics.set keeps the offset.
"""
from __future__ import annotations

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
SHOTS = os.environ.get("R41_SHOTS", "")
GOAL_MS = float(os.environ.get("R41_SYNC_GOAL_MS", "50"))

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

class ServerLyricsTest(unittest.TestCase):
    def lib(self, tmp, duration=200):
        from app.music import library
        lib = library.Library(Path(tmp))
        path = Path(tmp) / "a.opus"
        path.write_bytes(b"0")
        tid = lib.add({"file": str(path), "title": "Song", "artist": "X", "duration": duration})["id"]
        return lib, tid

    def test_duration_match_first_and_warning(self):
        from app.music import lyrics
        with tempfile.TemporaryDirectory() as tmp:
            lib, tid = self.lib(tmp)
            other = {"source": "lrclib", "title": "Song", "artist": "X", "duration": 212, "synced": [[1.0, "radio edit"], [2.0, "b"]], "plain": "radio edit", "instrumental": False}
            same = {"source": "netease", "title": "Song", "artist": "X", "duration": 201, "synced": [[1.0, "album"], [2.0, "b"]], "plain": "album", "instrumental": False}
            old = lyrics.PROVIDERS.copy()
            lyrics.PROVIDERS.update({"lrclib": lambda c, q: [dict(other)], "netease": lambda c, q: [dict(same)]})

            class Dummy:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False
            try:
                cands, _ = lyrics.search(lyrics.query_of(dict(lib.row(tid))), ["lrclib", "netease"], lambda: Dummy())
            finally:
                lyrics.PROVIDERS.clear()
                lyrics.PROVIDERS.update(old)
            self.assertEqual(cands[0]["source"], "netease")
            lyrics._save_meta(lib, tid, alts=cands, choice=1)
            lyrics._apply(lib, tid, cands[1])
            ans = lyrics._answer(lib, tid, True)
            self.assertEqual(ans["duration_diff"], 12.0)
            lyrics._save_meta(lib, tid, choice=0)
            lyrics._apply(lib, tid, cands[0])
            self.assertEqual(lyrics._answer(lib, tid, True)["duration_diff"], 1.0)
            lib.close()

    def test_manual_keeps_offset_and_general_offset(self):
        from app.music import lyrics
        with tempfile.TemporaryDirectory() as tmp:
            lib, tid = self.lib(tmp)
            lyrics.set_offset(lib, tid, 0.7)
            out = lyrics.set_manual(lib, tid, "[00:01.00]one\n[00:02.00]two")
            self.assertEqual(out["offset"], 0.7)
            self.assertEqual(out["source"], "manual")
            lib.set_settings({"lyrics_offset_ms": 250})
            self.assertEqual(lyrics._answer(lib, tid, True)["general_offset"], 0.25)
            lib.set_settings({"lyrics_offset_ms": 99999})
            self.assertEqual(lib.settings()["lyrics_offset_ms"], 2000)
            lib.close()

    def test_static(self):
        np_js = (ROOT / "app/module_builtins/music/np.js").read_text(encoding="utf-8")
        self.assertNotIn("+ .08", np_js)
        self.assertIn('data-ly="edit"', np_js)
        self.assertIn('data-ly="tap"', np_js)
        self.assertIn("Player.heard()", np_js)
        core = (ROOT / "app/module_builtins/music/feat-core.js").read_text(encoding="utf-8")
        self.assertIn('["ly_earlier", "BracketLeft"]', core)
        tg = (ROOT / "app/tgapp/music.js").read_text(encoding="utf-8")
        self.assertIn("function lyricLoop()", tg)
        self.assertNotIn("+ 0.1;   // r39", tg)

@unittest.skipUnless(sync_playwright and CHROMIUM and shutil.which("ffmpeg"), "needs playwright, Chromium and ffmpeg")
class LiveLyricsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import httpx
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        tones = root / "tones"
        tones.mkdir()
        for i, (freq, dur) in enumerate(((330, 40), (440, 40))):
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={dur}",
                            "-c:a", "libopus", "-b:a", "32k", str(tones / f"tone{i}.opus")], check=True)
        cls.port = _free_port()
        env = dict(os.environ, MEMUAI_DATA_DIR=str(root / "data"), MEMUAI_PROXY_URL="http://127.0.0.1:9", NO_PROXY="127.0.0.1,localhost")
        subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); from app import pgdata; "
                        "pgdata.save_preferences({'profile_onboarded': True, 'profile_name': 'Test'})" % str(ROOT)], env=env, check=True)
        code = ("import sys, uvicorn; sys.path.insert(0, %r); from app.playground_api import create_app; "
                "uvicorn.run(create_app(), host='127.0.0.1', port=%d, log_level='error')") % (str(ROOT), cls.port)
        cls.proc = subprocess.Popen([sys.executable, "-c", code], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.http = httpx.Client(trust_env=False, timeout=30)
        for _ in range(300):
            try:
                if cls.http.get(cls.base + "/api/pg/modules").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        cls.http.post(cls.base + "/api/pg/modules/configure", json={"id": "music", "enabled": True})
        cls.call("settings.set", {"auto_lyrics": False, "lyrics_offset_ms": 0})
        cls.call("import.folder", {"path": str(tones)})
        for _ in range(60):
            if (cls.call("library.list", {}) or {}).get("total") == 2:
                break
            time.sleep(0.5)
        tracks = sorted(cls.call("library.list", {})["tracks"], key=lambda t: t["file"] if "file" in t else t["id"])
        cls.ids = {}
        for track, title in zip(tracks, ("Alpha", "Bravo")):
            cls.call("library.update", {"id": track["id"], "title": title, "artist": "T"})
            cls.ids[title] = track["id"]
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(executable_path=CHROMIUM, args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"])
        cls.ctx = cls.browser.new_context()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.proc.terminate()
        try:
            cls.proc.wait(5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
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

    def open_module(self):
        app = self.ctx.new_page()
        app.set_viewport_size({"width": 1180, "height": 780})
        app.goto(self.base + "/?module_app=music")
        for _ in range(600):
            for f in app.frames[1:]:
                try:
                    if f.evaluate("() => !!(window.MemuMusic && MemuMusic.S.loaded && MemuMusic.S.tracks.length === 2)"):
                        return app, f
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(0.1)
        raise TimeoutError([f.url for f in app.frames])

    def shot(self, page, name):
        if SHOTS:
            Path(SHOTS).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(SHOTS) / name))

    def play_with_lyrics(self, f, tid, autoplay=True):
        f.evaluate("([id, a]) => { MemuMusic.Player.playList([MemuMusic.S.byId.get(id)], id, 'L', { autoplay: a }); MemuMusic.NP().open('lyrics'); }", [tid, autoplay])

    def test_1_edit_existing_lyrics(self):
        tid = self.ids["Alpha"]
        self.call("lyrics.set", {"id": tid, "text": "[00:01.00]First line\n[00:03.00]Second line\n[00:05.00]Third line"})
        self.call("lyrics.offset", {"id": tid, "offset": 0.3})
        app, f = self.open_module()
        self.play_with_lyrics(f, tid, False)
        f.wait_for_selector('#ly-bar [data-ly="edit"]', timeout=10000)
        self.shot(app, "lyrics-bar.png")
        f.click('#ly-bar [data-ly="edit"]')
        area = f.wait_for_selector(".mm-lyrics-paste textarea", timeout=5000)
        value = area.input_value()
        self.assertIn("[00:03.00]Second line", value)
        self.assertTrue(f.evaluate("() => document.getElementById('ly-bar').hidden"))
        f.fill(".mm-lyrics-paste textarea", value.replace("Second line", "Second line (fixed)"))
        self.shot(app, "lyrics-edit.png")
        f.click('#lyrics [data-ly="save"]')
        self.poll(f, "() => [...document.querySelectorAll('#lyrics-inner .mm-lyric')].some((n) => /fixed/.test(n.textContent))", 5000)
        got = self.call("lyrics.get", {"id": tid})
        self.assertEqual(got["source"], "manual")
        self.assertEqual(got["synced"][1][1], "Second line (fixed)")
        self.assertAlmostEqual(got["offset"], 0.3)
        self.assertIn("0.3", f.inner_text("#ly-bar").replace(",", "."))

        self.call("lyrics.set", {"id": self.ids["Bravo"], "text": "Just words\nmore words"})
        self.play_with_lyrics(f, self.ids["Bravo"], False)
        f.wait_for_selector('#ly-bar [data-ly="edit"]', timeout=10000)
        f.click('#ly-bar [data-ly="edit"]')
        self.assertEqual(f.input_value(".mm-lyrics-paste textarea"), "Just words\nmore words")
        f.fill(".mm-lyrics-paste textarea", "Just words\nedited")
        f.press(".mm-lyrics-paste textarea", "Control+Enter")
        self.poll(f, "() => /edited/.test(document.querySelector('#lyrics-inner').textContent)", 5000)

        app.close()
        app, f = self.open_module()
        self.play_with_lyrics(f, tid, False)
        self.poll(f, "() => /fixed/.test(document.querySelector('#lyrics-inner').textContent)", 10000)
        app.close()

    def measure_sync(self, motion):
        tid = self.ids["Alpha"]
        stamps = [1.0 + 0.9 * i for i in range(12)]
        self.call("lyrics.set", {"id": tid, "text": "\n".join(f"[00:{s:05.2f}]Line {i}" for i, s in enumerate(stamps))})
        self.call("lyrics.offset", {"id": tid, "offset": 0})
        app, f = self.open_module()
        f.evaluate("(m) => MemuMusic.Theme.set('motion', m)", motion)

        f.evaluate("() => { const st = document.createElement('style'); st.textContent = '*, *::before, *::after { animation-play-state: paused !important; }'; document.head.appendChild(st); }")
        self.play_with_lyrics(f, tid, False)
        f.wait_for_selector("#lyrics-inner .mm-lyric", timeout=10000)
        f.evaluate("""() => { const w = window.__r41s = { samples: [], switches: [], last: -1, lat: 0, ticks: 0 };
            const P = MemuMusic.Player;
            const tick = () => {
              w.ticks++;
              const a = P.times(); if (P.st.playing) w.samples.push([performance.now(), a.cur, P.heard()]);
              w.lat = P.latency();
              const on = document.querySelector('#lyrics-inner .mm-lyric.on'); const i = on ? +on.dataset.l : -1;
              if (i !== w.last) { w.switches.push([performance.now(), i]); w.last = i; }
              if (w.ticks < 2400) requestAnimationFrame(tick);
            };
            requestAnimationFrame(tick);
            P.seek(0); P.play(); }""")
        time.sleep(13)
        data = f.evaluate("() => window.__r41s")
        f.evaluate("() => MemuMusic.Player.pause()")
        app.close()
        samples = [s for s in data["samples"] if s[1] > 0.3]
        n = len(samples)
        self.assertGreater(n, 30, f"too few frames: {data['ticks']} ticks, {len(data['samples'])} while playing")
        mx = sum(s[0] for s in samples) / n
        my = sum(s[1] for s in samples) / n
        k = sum((s[0] - mx) * (s[1] - my) for s in samples) / sum((s[0] - mx) ** 2 for s in samples)
        clock = lambda t: my + k * (t - mx)   # noqa: E731  audio position at performance.now() = t
        errors = []
        for t, i in data["switches"]:
            if 0 <= i < len(stamps) and t >= samples[0][0]:
                errors.append(round((clock(t) - data["lat"] - stamps[i]) * 1000, 1))

        engine = [abs(s[2] - (clock(s[0]) - data["lat"])) * 1000 for s in samples[3:]]
        span = (samples[-1][0] - samples[0][0]) / 1000
        fps = n / span if span else 0
        print(f"\n[round41 lyrics sync · motion={motion}] {fps:.0f} fps, distinct currentTime {len({round(s[1], 4) for s in samples})}/{n}, "
              f"latency {data['lat'] * 1000:.1f} ms, rate {k * 1000:.4f}, engine max {max(engine):.1f} ms, line errors ms {errors}")
        self.assertGreaterEqual(len(errors), 8, data["switches"])
        return errors, fps, max(engine), max(b[0] - a[0] for a, b in zip(samples, samples[1:]))

    def test_2_sync_accuracy(self):
        """Each line lights up when the AUDIO is at its stamp: error vs the fitted audio clock, < 50 ms.
        A line is painted in the first frame after its moment, so the error is bounded by one frame: «lite» effects
        (the per-frame karaoke clock of the calm mode) keep a fast frame rate even in a software-rendered headless
        Chromium; the full effects are checked only when the sandbox renders them at ≥ 30 fps."""
        for motion in ("lite", "full"):
            errors, fps, engine, frame = self.measure_sync(motion)
            self.assertLess(engine, GOAL_MS)
            self.assertGreater(min(errors), -20, errors)

            self.assertLess(max(errors), GOAL_MS + frame + 5, errors)

    def test_3_offset_controls(self):
        tid = self.ids["Alpha"]
        self.call("lyrics.set", {"id": tid, "text": "[00:02.00]A\n[00:04.00]B\n[00:06.00]C"})
        self.call("lyrics.offset", {"id": tid, "offset": 0})
        app, f = self.open_module()
        self.play_with_lyrics(f, tid, False)
        f.wait_for_selector('#ly-bar [data-ly="off+"]', timeout=10000)
        f.click('#ly-bar [data-ly="off+"]')
        f.click('#ly-bar [data-ly="off+"]', modifiers=["Shift"])
        f.focus("#ly-bar")
        f.evaluate("() => document.activeElement && document.activeElement.blur()")
        f.press("body", "BracketLeft")
        f.press("body", "Shift+BracketRight")
        self.poll(f, "() => /\\+1[.,]0/.test(document.getElementById('ly-bar').textContent)", 3000)
        time.sleep(0.9)
        self.assertAlmostEqual(self.call("lyrics.get", {"id": tid})["offset"], 1.0)

        f.evaluate("() => { MemuMusic.Player.seek(0); MemuMusic.Player.play(); }")
        self.poll(f, "() => MemuMusic.Player.heard() > 3.0", 10000)
        f.click('#ly-bar [data-ly="tap"]')

        heard = f.evaluate("() => { const h = MemuMusic.Player.heard(); document.querySelector('#lyrics-inner .mm-lyric[data-l=\"1\"]').click(); return h; }")
        time.sleep(0.9)
        off = self.call("lyrics.get", {"id": tid})["offset"]
        self.assertAlmostEqual(off, heard - 4.0, delta=0.06)
        self.assertTrue(f.evaluate("() => document.querySelector('#lyrics-inner .mm-lyric[data-l=\"1\"]').classList.contains('on')"))
        f.evaluate("() => MemuMusic.Player.pause()")
        self.shot(app, "lyrics-offset.png")
        app.close()

if __name__ == "__main__":
    unittest.main()
