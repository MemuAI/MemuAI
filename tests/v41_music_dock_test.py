"""The dock must preserve its handoff position while media metadata loads."""
import os
from pathlib import Path
import shutil
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

CHROME = os.environ.get('MEMU_TEST_CHROMIUM') or shutil.which('chromium') or r'C:\Program Files\Google\Chrome\Application\chrome.exe'
SCRIPT = Path(__file__).resolve().parents[1] / 'app/pgstatic/music-dock.js'


@unittest.skipUnless(sync_playwright and Path(CHROME).is_file(), 'isolated Chromium / Playwright unavailable')
class DockHandoffTests(unittest.TestCase):
    def test_delayed_metadata_never_reports_zero_and_old_seek_cannot_win(self):
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=CHROME, headless=True, args=['--no-sandbox'])
            try:
                page = browser.new_page()
                page.set_content('<div class="side-bottom"></div>')
                page.evaluate('''() => {
                  window.PG_LANG = 'en'; window.reports = [];
                  window.MemuModules = {catalog:[{id:'music',enabled:true}],openIds:[],windowState:()=>null};
                  window.EventSource = class { addEventListener(){} close(){} };
                  window.Audio = class extends EventTarget {
                    constructor(){super(); window.testAudio=this; this.currentTime=0; this.duration=120; this.paused=true; this.playbackRate=1;}
                    set src(url){this._src=url;this.currentTime=0;} get src(){return this._src || '';}
                    play(){this.paused=false; this.dispatchEvent(new Event('play')); return Promise.resolve();}
                    pause(){this.paused=true; this.dispatchEvent(new Event('pause'));}
                  };
                  window.fetch = async (_url, opts) => {
                    const req = JSON.parse(opts.body || '{}'); let result = {};
                    if (req.op === 'player.state') result = {track_id:'first', position:42, position_now:42, playing:true, queue:['first'], track:{id:'first',title:'First',duration:120}};
                    if (req.op === 'stream_url') result = {url:'http://127.0.0.1/audio/'+req.args.id};
                    if (req.op === 'library.get') result = {id:req.args.id,title:req.args.id,duration:120};
                    if (req.op === 'player.update') window.reports.push(req.args);
                    return {ok:true,json:async()=>({result})};
                  };
                }''')
                page.add_script_tag(path=str(SCRIPT))
                page.evaluate('() => MemuMusicDock.boot(true)')
                page.evaluate('() => MemuMusicDock.takeOver(true)')
                page.wait_for_function('() => window.reports.length > 0')
                self.assertEqual(page.evaluate('MemuMusicDock.state().position'), 42)
                self.assertEqual(page.evaluate('window.reports[0].position'), 42)
                page.evaluate("() => MemuMusicDock.apply({cmd:'play_track', args:{track_id:'second', position:17}})")
                page.evaluate("() => testAudio.dispatchEvent(new Event('loadedmetadata'))")
                self.assertEqual(page.evaluate('testAudio.currentTime'), 17)
                page.evaluate('''() => {
                  const original = window.fetch;
                  window.fetch = (url, opts) => {
                    const req = JSON.parse(opts.body || '{}');
                    if (req.op === 'library.get' && ['third', 'fifth'].includes(req.args.id)) return new Promise(resolve => {
                      window.resolveDelayed = () => resolve({ok:true,json:async()=>({result:{id:req.args.id,title:req.args.id,duration:120}})});
                    });
                    return original(url, opts);
                  };
                  window.oldRequest = MemuMusicDock.apply({cmd:'play_track',args:{track_id:'third',position:9}});
                }''')
                page.wait_for_function('() => !!window.resolveDelayed')
                page.evaluate("() => MemuMusicDock.apply({cmd:'seek',args:{position:33}})")
                self.assertEqual(page.evaluate('MemuMusicDock.state().position'), 33)
                page.evaluate("() => testAudio.dispatchEvent(new Event('loadedmetadata'))")
                self.assertEqual(page.evaluate('MemuMusicDock.state().position'), 33)
                page.evaluate('() => { window.resolveDelayed(); return window.oldRequest; }')
                page.evaluate("() => testAudio.dispatchEvent(new Event('loadedmetadata'))")
                self.assertEqual(page.evaluate('testAudio.currentTime'), 33)
                page.evaluate("() => { window.resolveDelayed = null; window.oldRequest = MemuMusicDock.apply({cmd:'play_track',args:{track_id:'fifth',position:9}}); }")
                page.wait_for_function('() => !!window.resolveDelayed')
                page.evaluate("() => MemuMusicDock.apply({cmd:'play_track',args:{track_id:'fourth',position:23}})")
                page.evaluate('() => { window.resolveDelayed(); return window.oldRequest; }')
                self.assertEqual(page.evaluate('MemuMusicDock.state().track_id || MemuMusicDock.state().trackId'), 'fourth')
                self.assertTrue(page.evaluate("testAudio.src.endsWith('/fourth')"))
                page.evaluate("() => MemuMusicDock.apply({cmd:'seek',args:{position:31}})")
                page.evaluate("() => testAudio.dispatchEvent(new Event('loadedmetadata'))")
                self.assertEqual(page.evaluate('testAudio.currentTime'), 31)
            finally:
                browser.close()


if __name__ == '__main__':
    unittest.main()
