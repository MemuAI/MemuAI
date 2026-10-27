from pathlib import Path
import copy
import os
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import round41_music_lyrics_test as base
from app.music import lyrics, library

class RomanizationTest(unittest.TestCase):
    def test_multilingual_and_original_timing(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = library.Library(Path(tmp))
            path = Path(tmp) / 'a.opus'; path.write_bytes(b'0')
            tid = lib.add({'file': str(path), 'title': 'Song', 'artist': 'A'})['id']
            rows = [[1.0, '私は日本人! Привет', [[1.0, '私'], [1.5, 'は']], 2.5], [4.0, '안녕하세요!', None, 6.0], [7.0, '日本語', None, 8.0]]
            lib.store_lyrics(tid, rows, '私は日本人! Привет\n안녕하세요!\n日本語', 'manual')
            before = copy.deepcopy(lib.cached_lyrics(tid))
            out = lyrics.romanize(lib, tid)
            self.assertEqual(out['synced'][0], [1.0, 'watashi ha nihonjin! Привет', None, 2.5])
            self.assertEqual(out['synced'][1], [4.0, 'annyeonghaseyo!', None, 6.0])
            self.assertEqual(out['synced'][2], [7.0, 'nihongo', None, 8.0])
            self.assertEqual(lib.cached_lyrics(tid), before)
            self.assertEqual(lyrics.romanize_text('日本語', 'ja'), 'nihongo')
            self.assertEqual(lyrics.romanize_text('日本語', 'zh'), 'ri ben yu')
            self.assertEqual(lyrics.romanize_text('你好', 'ja'), '你好')
            self.assertEqual(lyrics.romanize_text('Latin, кириллица: [01:23.00]'), 'Latin, кириллица: [01:23.00]')
            with self.assertRaises(ValueError): lyrics.romanize(lib, tid, 'invalid')
            lib.close()

@unittest.skipUnless(base.sync_playwright and base.CHROMIUM and shutil.which('ffmpeg'), 'requires headless Chromium and ffmpeg')
class LyricsInteractionTest(unittest.TestCase):
    setUpClass = classmethod(base.LiveLyricsTest.setUpClass.__func__)
    tearDownClass = classmethod(base.LiveLyricsTest.tearDownClass.__func__)
    call = classmethod(base.LiveLyricsTest.call.__func__)
    poll = staticmethod(base.LiveLyricsTest.poll)
    open_module = base.LiveLyricsTest.open_module
    play_with_lyrics = base.LiveLyricsTest.play_with_lyrics

    def test_sources_romanization_and_mouse_edit(self):
        tid = self.ids['Alpha']
        lib = library.Library(Path(self.tmp.name) / 'data' / 'music')
        cands = [dict(source='lrclib', title='Alpha', artist='T', duration=40, synced=[[1, '私は日本人'], [3, '日本語']], plain='私は日本人\n日本語', instrumental=False), dict(source='qq', title='Alpha', artist='T', duration=40, synced=[[1, '你好'], [3, 'World']], plain='你好\nWorld', instrumental=False)]
        lyrics._save_meta(lib, tid, alts=cands, choice=0); lyrics._apply(lib, tid, cands[0]); lib.close()
        page, frame = self.open_module()
        try:
            self.play_with_lyrics(frame, tid, False)
            frame.wait_for_selector('#ly-bar [data-ly="roman"]')
            frame.click('#ly-bar [data-ly="alts"]')
            frame.click('#ly-bar [data-ly="pick"][data-i="1"]')
            self.poll(frame, "() => document.querySelector('#ly-bar .mm-ly-src span').textContent === 'QQ Music'")
            self.assertEqual(self.call('lyrics.get', {'id': tid})['source'], 'qq')
            frame.click('#ly-bar [data-ly="roman"]')
            self.poll(frame, "() => document.querySelector('#lyrics-inner').textContent.includes('ni hao')")
            self.assertEqual(self.call('lyrics.get', {'id': tid})['synced'][0][1], '你好')
            frame.evaluate('''() => {
              const original = MemuMusic.API.call;
              MemuMusic.API.call = async (op, args) => {
                const result = await original(op, args);
                if (op === 'lyrics.romanize' && args.language === 'auto' && !window.__heldRoman) {
                  window.__heldRoman = true;
                  await new Promise(resolve => { window.__resumeRoman = resolve; });
                }
                return result;
              };
            }''')
            frame.click('#ly-bar [data-ly="roman"]')
            frame.click('#ly-bar [data-ly="roman"]')
            self.poll(frame, '() => window.__heldRoman === true')
            frame.click('#ly-bar [data-ly="alts"]')
            frame.evaluate('window.__resumeRoman()')
            self.poll(frame, "() => document.querySelector('#lyrics-inner').textContent.includes('ni hao')")
            self.assertTrue(frame.locator('#ly-bar [data-ly="pick"][data-i="0"]').is_visible())
            frame.click('#ly-bar [data-ly="pick"][data-i="0"]')
            self.poll(frame, "() => document.querySelector('#lyrics-inner').textContent.includes('nihongo')")
            frame.click('#ly-bar [data-ly="edit"]')
            area = frame.locator('.mm-lyrics-paste textarea')
            self.assertIn('私は日本人', area.input_value())
            frame.evaluate("document.body.style.userSelect = 'none'")
            frame.evaluate("async () => { await Promise.all(document.querySelector('#np').getAnimations().map(a => a.finished.catch(() => {}))); }")
            original_text = area.input_value()
            area.fill('0123456789abcdef ' * 8)
            area.scroll_into_view_if_needed()
            box = area.bounding_box()
            style = area.evaluate("e => { const s=getComputedStyle(e); return {left:parseFloat(s.paddingLeft)+parseFloat(s.borderLeftWidth), top:parseFloat(s.paddingTop)+parseFloat(s.borderTopWidth), line:parseFloat(s.lineHeight)||parseFloat(s.fontSize)*1.2}; }")
            start = (box['x'] + style['left'] + 3, box['y'] + style['top'] + style['line'] / 2)
            end = (box['x'] + box['width'] - 20, start[1])
            page.mouse.move(*start); page.mouse.down(); page.mouse.move(*end, steps=12); page.mouse.up()
            self.assertIn('0123456789abcdef', area.evaluate('e => e.value.slice(e.selectionStart,e.selectionEnd)'))
            area.fill(original_text)
            area.click(button='right')
            frame.wait_for_selector('.mm-ly-edit-menu')
            frame.click('.mm-ly-edit-menu [data-action="select"]')
            self.assertEqual(frame.eval_on_selector('.mm-lyrics-paste textarea', 'e => e.selectionEnd - e.selectionStart'), len(area.input_value()))
            frame.evaluate('''() => {
              Object.defineProperty(navigator, 'clipboard', {value: undefined, configurable: true});
              let text = ''; Memu.copy = async value => { text = value; }; Memu.paste = async () => text;
            }''')
            area.click(button='right'); frame.click('.mm-ly-edit-menu [data-action="copy"]')
            area.fill('replacement'); area.click(button='right'); frame.click('.mm-ly-edit-menu [data-action="select"]')
            area.click(button='right'); frame.click('.mm-ly-edit-menu [data-action="paste"]')
            self.poll(frame, "() => document.querySelector('.mm-lyrics-paste textarea').value.includes('私は日本人')")
        finally: page.close()

if __name__ == '__main__': unittest.main()
