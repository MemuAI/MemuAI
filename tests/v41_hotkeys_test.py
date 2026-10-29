"""Hotkey recording survives an asynchronous settings refresh."""
import os
from pathlib import Path
import shutil
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

CHROME = (os.environ.get("MEMU_TEST_CHROMIUM") or shutil.which("chromium")
          or r"C:\Program Files\Google\Chrome\Application\chrome.exe")
SCRIPT = Path(__file__).resolve().parents[1] / "app/pgstatic/hotkeys.js"


@unittest.skipUnless(sync_playwright and Path(CHROME).is_file(), "isolated Chromium / Playwright unavailable")
class RecordingTests(unittest.TestCase):
    def test_delayed_globals_preserve_active_button_and_capture(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=CHROME, headless=True, args=["--no-sandbox"])
            try:
                page = browser.new_page()
                page.set_content('<div id="hk-root"></div>')
                page.evaluate("""() => {
                  window.PG_LANG = 'en'; window.savedPrefs = {};
                  window.pref = (key, fallback) => window.savedPrefs[key] ?? fallback;
                  window.savePreferences = patch => Object.assign(window.savedPrefs, patch);
                  window.toast = () => {};
                  window.pending = []; window.done = 0; window.handled = 0;
                  window.fetch = () => new Promise(resolve => window.pending.push(() => resolve({
                    ok: true, json: async () => { window.done++; return {}; }
                  })));
                }""")
                page.add_script_tag(path=str(SCRIPT))
                page.evaluate("() => { window.addEventListener('keydown', e => { if (e.code === 'F12') window.handled++; }, true); MemuHotkeys.mountSettings(); }")
                self.assertEqual(page.evaluate("window.pending.length"), 4)
                button = page.locator("[data-hk-record]").first
                key = button.get_attribute("data-hk-record")
                button.click()
                self.assertEqual(button.get_attribute("aria-pressed"), "true")
                page.evaluate("() => window.pending.splice(0).forEach(resolve => resolve())")
                page.wait_for_function("() => window.done === 4")
                page.evaluate("() => new Promise(resolve => setTimeout(resolve, 0))")
                self.assertTrue(page.evaluate("MemuHotkeys.recording"))
                self.assertEqual(button.get_attribute("aria-pressed"), "true")
                page.keyboard.press("Control+Alt+F12")
                self.assertFalse(page.evaluate("MemuHotkeys.recording"))
                self.assertEqual(page.evaluate("key => window.savedPrefs.shortcuts[key]", key), "Ctrl+Alt+F12")
                self.assertEqual(page.evaluate("window.handled"), 0)
                self.assertEqual(page.locator(".hk-record.recording").count(), 0)
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
