"""Round 41 (3.7, r41b-lyrics): MemuMusic karaoke follows the SUNG part of a line, not the gap to the next stamp.

- LyricTiming (app/module_builtins/music/lyrics-sync.js, run in node): the end of a line from the source (4th item),
  word timing, an empty «[mm:ss.xx]» pause marker, or syllables at the song's own singing rate; pauses ≥ 4.5 s → «•••»;
- the server: enhanced LRC trailing «<mm:ss.xx>» / «[mm:ss.xx]», Musixmatch richsync «te», NetEase yrc (word + line
  durations), the «lyrics_sync» setting (smart | line | off);
- Chromium: «By word» stops filling when the line is sung and shows the dots in the break, «By line» lights the whole
  line, «Off» is a plain scrollable text; the mode switches live; the editor's «Line end» writes a pause marker.
Offline: the sources are mocked, the audio is a generated tone.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
MUSIC = ROOT / "app/module_builtins/music"
NODE = shutil.which("node")

import round41_music_lyrics_test as base  # noqa: E402  (the live harness: server + Chromium + tones)

LONG_GAP = [[1.0, "Everything that I can say is spoken for me, yeah"], [21.0, "And I keep on running through the night"],
            [24.2, "Every light is calling out my name"], [27.4, "Hold on, hold on"], [30.0, "Never let it go"]]

def timing(script: str):
    """Run `script` (JS returning a JSON-able value) with LyricTiming loaded, in node."""
    code = ("const vm = require('vm'); const ctx = vm.createContext({}); vm.runInContext(require('fs').readFileSync(%s, 'utf8')"
            " + ';globalThis.L = LyricTiming;', ctx); process.stdout.write(JSON.stringify(vm.runInContext(%s, ctx)));"
            % (json.dumps(str(MUSIC / "lyrics-sync.js")), json.dumps("(() => {" + script + "})()")))
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=30)
    if out.returncode:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)

@unittest.skipUnless(NODE, "needs node")
class TimingTest(unittest.TestCase):
    def test_long_break_ends_the_line_early_and_shows_dots(self):
        t = timing("return L.build(%s, 40);" % json.dumps(LONG_GAP))
        first = t["lines"][0]
        self.assertGreater(first["end"], 4.5)
        self.assertLess(first["end"], 11.0)
        self.assertEqual(len(t["gaps"]), 1)
        self.assertEqual(t["gaps"][0]["after"], 0)
        self.assertEqual(t["gaps"][0]["to"], 21.0)
        for a, b in zip(t["lines"][1:], t["lines"][2:]):
            self.assertEqual(a["end"], b["start"])
        at = timing("const t = L.build(%s, 40); return [L.at(t, 4), L.at(t, 13), L.at(t, 20.8), L.at(t, 22)];" % json.dumps(LONG_GAP))
        self.assertTrue(0.2 < at[0]["progress"] < 0.8 and at[0]["gap"] is None)
        self.assertEqual(at[1]["line"], 0)
        self.assertEqual(at[1]["progress"], 1)
        self.assertTrue(at[1]["gap"] and 0.2 < at[1]["gapProgress"] < 0.6)
        self.assertLess(at[2]["gapLeft"], 0.55)
        self.assertEqual((at[3]["line"], at[3]["gap"]), (1, None))

    def test_sources_of_the_end(self):
        rows = [[2.0, "Explicit end from the source", None, 5.5], [20.0, "Word timed", [[20.0, "Word "], [20.6, "timed"]]],
                [30.0, "Before a pause marker"], [33.0, ""], [45.0, "yrc words", [[45.0, "yrc ", 45.4], [45.4, "words", 46.2]], 46.2],
                [50.0, "last"]]
        t = timing("return L.build(%s, 60);" % json.dumps(rows))
        ends = [ln["end"] for ln in t["lines"]]
        self.assertEqual(ends[0], 5.5)
        self.assertTrue(20.9 < ends[1] < 23.0, ends)
        self.assertEqual(ends[2], 33.0)
        self.assertEqual(ends[3], 46.2)
        self.assertEqual(t["lines"][3]["words"][0]["end"], 45.4)
        self.assertEqual([ln["row"] for ln in t["lines"]], [0, 1, 2, 4, 5])
        self.assertEqual([g["after"] for g in t["gaps"]], [0, 1, 2])

    def test_intro_dots_syllables_and_rate(self):
        t = timing("return L.build([[12, 'first words here'], [14, 'next one']], 30);")
        self.assertEqual(t["gaps"][0]["after"], -1)
        self.assertEqual(timing("return [L.syllables('我爱你中国'), L.syllables('make some noise'), L.syllables('Привет, как дела')];"), [5, 3, 5])
        fast = [[i * 1.2, "ta ta ta ta ta ta ta ta ta ta ta ta"] for i in range(10)]
        self.assertEqual(timing("return L.rate(%s);" % json.dumps(fast)), 7)
        self.assertEqual(timing("return L.rate([[0, 'a'], [9, 'b']]);"), 3.6)

class ServerTest(unittest.TestCase):
    def test_lrc_line_ends(self):
        from app.music import lyrics as L
        rows = L.parse_lrc("[00:01.00]<00:01.00>Hello <00:01.50>world<00:02.40>\n[00:03.00]plain[00:05.20]\n[00:06.00]\n[00:07.00]x")
        self.assertEqual(rows[0], [1.0, "Hello world", [[1.0, "Hello "], [1.5, "world"]], 2.4])
        self.assertEqual(rows[1], [3.0, "plain", None, 5.2])
        self.assertEqual(rows[2], [6.0, ""])
        self.assertEqual(rows[3], [7.0, "x"])

    def test_yrc_and_netease(self):
        from app.music import lyrics as L
        yrc = '{"t":0,"c":[{"tx":"credits"}]}\n[1000,2500](1000,500,0)I (1500,400,0)can(1900,1600,0) go\n[9000,1000](9000,1000,0)end'
        self.assertEqual(L.parse_yrc(yrc)[0], [1.0, "I can go", [[1.0, "I ", 1.5], [1.5, "can", 1.9], [1.9, " go", 3.5]], 3.5])

        class Resp:
            def __init__(self, data):
                self.status_code, self.data = 200, data

            def json(self):
                return self.data

        class Client:
            def get(self, url, params=None, headers=None):
                if "cloudsearch" in url:
                    return Resp({"result": {"songs": [{"id": 7, "name": "Song", "ar": [{"name": "X"}], "dt": 200000}]}})
                assert params.get("yv") == 1
                return Resp({"lrc": {"lyric": "[00:01.00]I can go\n[00:09.00]end"}, "yrc": {"lyric": yrc}})
        got = L._netease(Client(), {"title": "Song", "artist": "X", "duration": 200})
        self.assertEqual(got[0]["synced"][0][3], 3.5)

    def test_setting_and_answer(self):
        from app.music import library, lyrics
        with tempfile.TemporaryDirectory() as tmp:
            lib = library.Library(Path(tmp))
            (Path(tmp) / "a.opus").write_bytes(b"0")
            tid = lib.add({"file": str(Path(tmp) / "a.opus"), "title": "Song", "artist": "X", "duration": 60})["id"]
            self.assertEqual(lib.settings()["lyrics_sync"], "smart")
            lib.set_settings({"lyrics_sync": "line"})
            self.assertEqual(lib.settings()["lyrics_sync"], "line")
            with self.assertRaises(ValueError):
                lib.set_settings({"lyrics_sync": "karaoke"})
            out = lyrics.set_manual(lib, tid, "[00:01.00]one\n[00:04.00]\n[00:10.00]two")
            self.assertEqual(out["synced"][1], [4.0, ""])
            self.assertFalse(out["words"])
            lib.close()

    def test_static(self):
        main = (MUSIC / "main.js").read_text(encoding="utf-8")
        self.assertLess(main.index("@memu-part:lyrics-sync.js"), main.index("@memu-part:np.js"))
        i18n = json.loads((MUSIC / "i18n.json").read_text(encoding="utf-8"))
        for lang in ("ru", "en"):
            for key in ("set.lyrics_sync", "set.lyrics_sync_hint", "set.lyrics_sync.smart", "set.lyrics_sync.line", "set.lyrics_sync.off",
                        "lyrics.end", "lyrics.end_hint"):
                self.assertTrue(i18n[lang].get(key), (lang, key))
        np_js = (MUSIC / "np.js").read_text(encoding="utf-8")
        self.assertIn('syncMode() !== "off" && Player.st.playing', np_js)
        self.assertNotIn("$$(\".w\", node)", np_js)
        css = (MUSIC / "style.css").read_text(encoding="utf-8")
        self.assertIn("html[data-calm] .mm-ly-gap.on { animation: none; }", css)

@unittest.skipUnless(base.sync_playwright and base.CHROMIUM and shutil.which("ffmpeg"), "needs playwright, Chromium and ffmpeg")
class LiveSyncTest(unittest.TestCase):
    setUpClass = classmethod(base.LiveLyricsTest.setUpClass.__func__)
    tearDownClass = classmethod(base.LiveLyricsTest.tearDownClass.__func__)
    call = classmethod(base.LiveLyricsTest.call.__func__)
    poll = staticmethod(base.LiveLyricsTest.poll)
    open_module = base.LiveLyricsTest.open_module
    play_with_lyrics = base.LiveLyricsTest.play_with_lyrics

    def at(self, f, sec):
        f.evaluate("(t) => { MemuMusic.Player.seek(t); MemuMusic.NP().syncLyrics(t, true); }", sec)
        time.sleep(0.25)
        f.evaluate("(t) => MemuMusic.NP().syncLyrics(t, true)", sec)
        return f.evaluate("""() => { const on = document.querySelector('#lyrics-inner .mm-lyric.on'), gap = document.querySelector('.mm-ly-gap.on');
          const first = document.querySelector('#lyrics-inner .mm-lyric');
          return { on: on ? +on.dataset.l : -1, lp: on ? +on.style.getPropertyValue('--lp') : null, gap: gap ? +gap.dataset.g : null,
            gp: gap ? +gap.style.getPropertyValue('--gp') : null, end: !!(gap && gap.classList.contains('end')),
            just: first.classList.contains('just'), sync: document.getElementById('lyrics').dataset.sync,
            stat: document.getElementById('lyrics').classList.contains('static'), gaps: document.querySelectorAll('.mm-ly-gap').length,
            bg: on ? getComputedStyle(on.firstElementChild).backgroundImage : '' }; }""")

    def test_1_modes_and_dots(self):
        tid = self.ids["Alpha"]
        self.call("settings.set", {"lyrics_sync": "smart"})
        self.call("lyrics.set", {"id": tid, "text": "\n".join(f"[00:{s:05.2f}]{line}" for s, line in LONG_GAP)})
        self.call("lyrics.offset", {"id": tid, "offset": 0})
        app, f = self.open_module()
        self.play_with_lyrics(f, tid, False)
        f.wait_for_selector("#lyrics-inner .mm-lyric", timeout=10000)
        s = self.at(f, 4.0)
        self.assertEqual((s["sync"], s["on"], s["gap"]), ("smart", 0, None))
        self.assertTrue(0.25 < s["lp"] < 0.8, s)
        self.assertEqual(s["gaps"], 1)
        s = self.at(f, 13.0)
        self.assertEqual((s["on"], s["gap"]), (-1, 0))
        self.assertTrue(s["just"] and 0.2 < s["gp"] < 0.6, s)
        self.assertTrue(self.at(f, 20.8)["end"])
        s = self.at(f, 23.0)
        self.assertEqual((s["on"], s["gap"]), (1, None))

        f.evaluate("() => MemuMusicFeat.Cfg.set({ lyrics_sync: 'line' })")
        self.poll(f, "() => document.getElementById('lyrics').dataset.sync === 'line'", 5000)
        s = self.at(f, 4.0)
        self.assertEqual((s["on"], s["bg"]), (0, "none"))
        self.assertEqual(self.at(f, 13.0)["gap"], 0)

        f.evaluate("() => MemuMusicFeat.Cfg.set({ lyrics_sync: 'off' })")
        self.poll(f, "() => document.getElementById('lyrics').dataset.sync === 'off'", 5000)
        s = self.at(f, 4.0)
        self.assertEqual((s["on"], s["gaps"], s["stat"]), (-1, 0, True))
        self.assertEqual(self.call("settings.get", {})["lyrics_sync"], "off")
        f.evaluate("() => MemuMusicFeat.Cfg.set({ lyrics_sync: 'smart' })")
        app.close()

    def test_2_words_and_line_end_in_editor(self):
        tid = self.ids["Bravo"]
        self.call("settings.set", {"lyrics_sync": "smart"})
        self.call("lyrics.set", {"id": tid, "text": "[00:01.00]<00:01.00>One <00:01.60>two <00:02.20>three<00:03.00>\n[00:12.00]Next line\n[00:14.00]And more"})
        app, f = self.open_module()
        self.play_with_lyrics(f, tid, False)
        f.wait_for_selector("#lyrics-inner .mm-lyric.words", timeout=10000)
        self.at(f, 1.8)
        self.assertEqual(f.evaluate("() => [...document.querySelectorAll('.mm-lyric.on .w')].map((w) => w.className)"), ["w sung", "w on", "w"])
        s = self.at(f, 5.0)
        self.assertEqual((s["on"], s["gap"]), (-1, 0))

        f.click('#ly-bar [data-ly="edit"]')
        area = f.wait_for_selector(".mm-lyrics-paste textarea", timeout=5000)
        self.assertIn("three<00:03.00>", area.input_value())
        f.evaluate("() => { const a = document.querySelector('.mm-lyrics-paste textarea'); const i = a.value.indexOf('Next line'); a.setSelectionRange(i, i); }")
        f.evaluate("() => MemuMusic.Player.seek(13.25)")
        time.sleep(0.3)
        f.click('#lyrics [data-ly="end"]')
        value = area.input_value()
        self.assertRegex(value, r"\[00:12\.00\]Next line\n\[00:13\.2\d\]\n\[00:14\.00\]And more")
        f.click('#lyrics [data-ly="save"]')
        self.poll(f, "() => !document.querySelector('.mm-lyrics-paste') && document.querySelectorAll('#lyrics-inner .mm-lyric').length === 3", 5000)
        synced = self.call("lyrics.get", {"id": tid})["synced"]
        self.assertEqual(synced[2][1], "")
        self.assertAlmostEqual(synced[2][0], 13.25, delta=0.06)
        app.close()

if __name__ == "__main__":
    unittest.main()
