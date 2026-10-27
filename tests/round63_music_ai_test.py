"""MemuMusic AI: real module UI and bridge exercised offline in a sandboxed iframe."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from playwright.sync_api import sync_playwright
import round37_music_harness as harness

PARENT = harness.PARENT.replace('  if (d.action === "open")', '''  if (d.action === "models") return reply(d.id, [{id:"test-chat", name:"Test model", kind:"chat", default:!window.__noDefault}, {id:"image-only", name:"Image", kind:"image", image:true}]);
  if (d.action === "search") return reply(d.id, {results:[{url:"https://lyrics.example/song", title:"Song", snippet:"Hello there"}]});
  if (d.action === "fetch") return window.__blockFetch ? reply(d.id, null, "blocked") : reply(d.id, {text:"Example lyrics: Hello there"});
  if (d.action === "request") { window.__requests = (window.__requests || []).concat([p]); return reply(d.id, {text: p.instructions.includes("music discovery") ? JSON.stringify({tracks:[{title:"One More Time",artist:"Daft Punk",reason:"Electronic"}]}) : JSON.stringify({lyrics:"Hello there",source:"https://lyrics.example/song"})}); }
  if (d.action === "open")''')

class MusicAI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        executable = os.environ.get("MEMU_TEST_CHROMIUM") or (r"C:\Program Files\Google\Chrome\Application\chrome.exe" if os.name == "nt" else None)
        cls.browser = cls.pw.chromium.launch(headless=True, executable_path=executable, args=["--no-sandbox"])
        cls.doc = harness.module_html("en", "dark")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 860})
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.route("http://harness.local/", lambda route: route.fulfill(status=200, content_type="text/html", body=PARENT))
        self.page.route("http://harness.local/module?mock=1", lambda route: route.fulfill(status=200, content_type="text/html", body=self.doc))
        self.page.goto("http://harness.local/")
        self.frame = harness.frame_of(self.page)
        harness.wait(self.page, self.frame, "() => !!(window.MemuMusic && MemuMusic.S.loaded)")

    def tearDown(self):
        self.assertEqual(self.errors, [])
        self.context.close()

    def wait(self, script):
        harness.wait(self.page, self.frame, script)

    def test_opt_in_models_and_recommendations(self):
        f = self.frame
        self.assertEqual(f.locator('[data-go="ai_recommendations"]').count(), 0)
        f.evaluate("() => MemuMusic.go('settings', {tab:'music-ai'})")
        self.wait("() => !!document.querySelector('[data-ai-enabled]')")
        self.assertIn("provider", f.locator("#set-body").inner_text().lower())
        self.wait("() => document.querySelector('[data-ai-picker=recModel] summary').textContent.includes('Test model')")
        f.locator('[data-ai-picker="recModel"] summary').click()
        self.assertEqual(f.locator('[data-model="image-only"]').count(), 0)
        f.locator('[data-ai-enabled]').check()
        f.locator('[data-go="ai_recommendations"]').click()
        f.locator('[data-ai-run]').click()
        self.wait("() => !!document.querySelector('.mm-ai-results .mm-orow')")
        self.assertIn("One More Time", f.locator('.mm-ai-results').inner_text())
        f.evaluate("() => { MemuMusic.S.prefs.musicAI.results[0].in_library=''; MemuMusic.go('ai_recommendations', {}, {force:true}); }")
        self.wait("() => !!document.querySelector('.mm-ai-results [data-play]')")
        self.assertIn("Electronic", f.locator('.mm-ai-results').inner_text())
        for _ in range(50):
            if self.page.evaluate("() => !!window.__saved.prefs?.musicAI?.results?.length"): break
            self.page.wait_for_timeout(100)
        self.assertTrue(self.page.evaluate("() => !!window.__saved.prefs?.musicAI?.results?.length"))
        self.assertTrue(self.page.evaluate("() => window.__requests.length === 1 && window.__requests[0].model === 'test-chat'"))
        self.assertEqual(f.locator('.mm-ai-results [data-play]').count(), 1)
        f.evaluate("() => MemuMusic.go('home')")
        f.evaluate("() => MemuMusic.go('ai_recommendations')")
        self.wait("() => !!document.querySelector('.mm-ai-results .mm-orow')")
        self.assertIn("One More Time", f.locator('.mm-ai-results').inner_text())
        f.evaluate('''() => { const old=MemuMusic.API.call; window.streamCalls=[]; MemuMusic.API.call=(op,args)=>{
          window.streamCalls.push(op); if(op==='online.stream') return Promise.resolve({url:'data:audio/wav;base64,UklGRg=='}); return old(op,args);
        }; }''')
        f.locator('.mm-ai-results [data-play]').click()
        self.wait("() => MemuMusic.Player.current()?.online_url")
        self.assertTrue(f.evaluate("() => window.streamCalls.includes('online.stream') && !window.streamCalls.includes('download.start')"))

    def test_lyrics_are_reviewed_before_save(self):
        f = self.frame
        f.evaluate("""() => { const original = MemuMusic.API.call; MemuMusic.API.call = (op, args) => {
          if (op === 'lyrics.set') { window.testSaved = args; return Promise.resolve({plain: args.text, synced: null, source: 'manual', alts: []}); }
          return original(op, args);
        }; }""")
        f.evaluate("() => { const S=MemuMusic.S; MemuMusic.Player.playList([S.tracks[0]], S.tracks[0].id, 'T'); MemuMusic.NP().open('lyrics'); }")
        self.wait("() => !!document.querySelector('#ly-bar [data-ly=alts]')")
        f.locator('#ly-bar [data-ly=alts]').click()
        self.wait("() => !!document.querySelector('#ly-bar [data-ly=ai]')")
        f.locator('#ly-bar [data-ly=ai]').click()
        f.locator('[data-find]').click()
        self.wait("() => !!document.querySelector('.mm-ai-lyrics')")
        self.assertEqual(f.locator('.mm-ai-lyrics').input_value(), 'Hello there')
        self.assertTrue(self.page.evaluate("() => window.__calls.includes('search') && window.__calls.includes('fetch')"))
        self.assertFalse(f.evaluate("() => !!window.testSaved"))
        f.locator('.mm-ai-lyrics').fill('Edited words')
        f.locator('[data-find]').click()
        self.wait("() => document.querySelector('#modal').hidden")
        self.assertEqual(f.evaluate("() => window.testSaved.text"), 'Edited words')

    def test_first_text_model_is_fallback_when_default_missing(self):
        self.page.evaluate("() => window.__noDefault = true")
        self.frame.evaluate("() => MemuMusic.go('settings', {tab:'music-ai'})")
        self.wait("() => document.querySelector('[data-ai-picker=recModel] summary')?.textContent.includes('Test model')")
        self.frame.locator('[data-ai-enabled]').check()
        self.frame.locator('[data-go="ai_recommendations"]').click()
        self.frame.locator('[data-ai-run]').click()
        self.wait("() => !!document.querySelector('.mm-ai-results .mm-orow')")
        self.assertEqual(self.page.evaluate("() => window.__requests[0].model"), 'test-chat')

    def test_blocked_pages_use_model_search(self):
        self.page.evaluate("() => window.__blockFetch = true")
        f = self.frame
        f.evaluate("() => { const S=MemuMusic.S; MemuMusic.Player.playList([S.tracks[0]], S.tracks[0].id, 'T'); MemuMusic.NP().open('lyrics'); }")
        self.wait("() => !!document.querySelector('#ly-bar [data-ly=alts]')")
        f.locator('#ly-bar [data-ly=alts]').click()
        f.locator('#ly-bar [data-ly=ai]').click()
        f.locator('[data-find]').click()
        self.wait("() => !!document.querySelector('.mm-ai-lyrics')")
        self.assertTrue(self.page.evaluate("() => window.__requests[0].web_search === true"))

if __name__ == '__main__':
    unittest.main()
