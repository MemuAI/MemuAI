from pathlib import Path
import shutil
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import round41_music_lyrics_test as base


@unittest.skipUnless(base.sync_playwright and base.CHROMIUM and shutil.which('ffmpeg'), 'requires isolated Chromium and ffmpeg')
class LyricsRegressionTest(unittest.TestCase):
    setUpClass = classmethod(base.LiveLyricsTest.setUpClass.__func__)
    tearDownClass = classmethod(base.LiveLyricsTest.tearDownClass.__func__)
    call = classmethod(base.LiveLyricsTest.call.__func__)
    poll = staticmethod(base.LiveLyricsTest.poll)
    open_module = base.LiveLyricsTest.open_module
    play_with_lyrics = base.LiveLyricsTest.play_with_lyrics

    def wait_offset(self, tid, expected):
        deadline = time.monotonic() + 5
        actual = None
        while time.monotonic() < deadline:
            actual = self.call('lyrics.get', {'id': tid})['offset']
            if actual == expected:
                return
            time.sleep(.05)
        self.assertEqual(actual, expected)

    def test_short_text_can_be_corrected_and_cleared(self):
        tid = self.ids['Alpha']
        self.call('lyrics.set', {'id': tid, 'text': 'f'})
        page, frame = self.open_module()
        try:
            self.play_with_lyrics(frame, tid, False)
            frame.wait_for_selector('#ly-bar [data-ly="edit"]')
            frame.click('#ly-bar [data-ly="edit"]')
            self.assertEqual(frame.input_value('.mm-lyrics-paste textarea'), 'f')
            frame.fill('.mm-lyrics-paste textarea', 'Correct text\nSecond line')
            frame.click('[data-ly="save"]')
            frame.wait_for_selector('#ly-bar [data-ly="edit"]')
            self.assertEqual(self.call('lyrics.get', {'id': tid})['plain'], 'Correct text\nSecond line')
            frame.click('#ly-bar [data-ly="edit"]')
            frame.fill('.mm-lyrics-paste textarea', '')
            frame.click('[data-ly="save"]')
            frame.wait_for_selector('#ly-bar [data-ly="edit"]')
            self.assertFalse(self.call('lyrics.get', {'id': tid})['plain'])
        finally:
            page.close()

    def test_line_button_exact_reset_and_track_switch(self):
        first, second = self.ids['Alpha'], self.ids['Bravo']
        for tid in (first, second):
            self.call('lyrics.set', {'id': tid, 'text': '[00:01.00]First line\n[00:05.00]Second line'})
        self.call('settings.set', {'lyrics_sync': 'smart'})
        self.call('lyrics.offset', {'id': first, 'offset': .03})
        self.call('lyrics.offset', {'id': second, 'offset': .7})
        page, frame = self.open_module()
        try:
            self.play_with_lyrics(frame, first, False)
            frame.wait_for_selector('#ly-bar [data-ly="sync"]')
            frame.click('#ly-bar [data-ly="sync"]')
            self.poll(frame, "() => document.getElementById('lyrics').dataset.sync === 'line'", 5000)
            frame.evaluate('MemuMusic.Player.seek(2); MemuMusic.NP().syncLyrics(2,true)')
            self.assertEqual(frame.eval_on_selector('.mm-lyric.on > span', 'e => getComputedStyle(e).backgroundImage'), 'none')
            frame.click('#ly-bar [data-ly="off-reset"]')
            self.wait_offset(first, 0)
            frame.click('#ly-bar [data-ly="off+"]')
            self.play_with_lyrics(frame, second, False)
            self.wait_offset(first, .1)
            self.assertEqual(self.call('lyrics.get', {'id': second})['offset'], .7)
        finally:
            page.close()

    def test_loading_and_failed_save_do_not_erase_draft(self):
        tid = self.ids['Bravo']
        self.call('lyrics.set', {'id': tid, 'text': 'Existing lyrics'})
        page, frame = self.open_module()
        try:
            self.play_with_lyrics(frame, tid, False)
            frame.wait_for_selector('#ly-bar [data-ly="edit"]')
            frame.evaluate('''() => {
              window.originalMusicCall = Memu.music.call;
              Memu.music.call = async (op,args) => {
                if(op === 'lyrics.get') await new Promise(resolve => setTimeout(resolve,800));
                if(op === 'lyrics.set') throw new Error('Simulated connection failure');
                return originalMusicCall(op,args);
              };
              MemuMusic.NP().reloadLyrics(MemuMusic.Player.current().id);
            }''')
            frame.click('#ly-bar [data-ly="edit"]')
            frame.fill('.mm-lyrics-paste textarea', 'Unsaved draft')
            time.sleep(1)
            self.assertEqual(frame.input_value('.mm-lyrics-paste textarea'), 'Unsaved draft')
            frame.click('[data-ly="save"]')
            self.poll(frame, "() => !document.querySelector('[data-ly=save]').disabled", 5000)
            self.assertEqual(frame.input_value('.mm-lyrics-paste textarea'), 'Unsaved draft')
            frame.evaluate('() => { Memu.music.call = originalMusicCall; }')
            frame.click('[data-ly="cancel"]')
            self.poll(frame, "() => document.getElementById('lyrics-inner').textContent.includes('Existing lyrics')", 5000)
        finally:
            page.close()


if __name__ == '__main__':
    unittest.main()
