"""Language changes update dynamically inserted navigation in the real module bridge."""
import os
import sys
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
import round37_music_harness as harness


class SearchNavLanguageTest(unittest.TestCase):
    def test_language_switch_updates_search_button(self):
        with sync_playwright() as pw:
            executable = os.environ.get("MEMU_TEST_CHROMIUM") or (r"C:\Program Files\Google\Chrome\Application\chrome.exe" if os.name == "nt" else harness.chromium())
            browser = pw.chromium.launch(headless=True, executable_path=executable, args=["--no-sandbox"])
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 800})
                doc = harness.module_html("ru", "dark")
                page.route("http://harness.local/", lambda route: route.fulfill(status=200, content_type="text/html", body=harness.PARENT))
                page.route("http://harness.local/module?mock=1", lambda route: route.fulfill(status=200, content_type="text/html", body=doc))
                page.goto("http://harness.local/")
                frame = harness.frame_of(page)
                harness.wait(page, frame, "() => !!(window.MemuMusic && MemuMusic.S.loaded)")
                nav = frame.locator('#nav [data-go="search"] span')
                self.assertEqual(nav.inner_text(), "Поиск")
                page.evaluate("() => document.getElementById('f').contentWindow.postMessage({type:'memu-module-host',action:'language',payload:{lang:'en'}}, '*')")
                harness.wait(page, frame, "() => document.querySelector('#nav [data-go=search] span').textContent === 'Search'")
                page.evaluate("() => document.getElementById('f').contentWindow.postMessage({type:'memu-module-host',action:'language',payload:{lang:'ru'}}, '*')")
                harness.wait(page, frame, "() => document.querySelector('#nav [data-go=search] span').textContent === 'Поиск'")
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
