"""Round 27 (r27-hunt-ui-3): look regressions found on the headless screenshots (round27_ui_look_shots.py)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-r27-look-")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from round27_ui_lang_test import _chromium, _has_playwright  # noqa: E402


@unittest.skipUnless(_chromium() and _has_playwright(), "headless Chromium / playwright is not available")
class GridNarrowModelPickerTest(unittest.TestCase):
    """Grid design at 720 px: the top bar caps the model picker at 180px and the «МОДЕЛЬ / MODEL» caption
    took a quarter of it — «demo-gpt-test» showed as «demo-gpt-t…» with the bar half empty."""

    def test_model_name_is_not_cut(self) -> None:
        import httpx
        from ui_lang_scan import Servers
        from playwright.sync_api import sync_playwright
        servers = Servers().start()
        try:
            with sync_playwright() as play:
                browser = play.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
                for lang in ("ru", "en"):
                    httpx.post(servers.pg + "/api/pg/preferences", json={
                        "profile_onboarded": True, "profile_name": "Alex", "language": lang, "ui_design": "grid",
                        "ui_motion": "reduced"}, trust_env=False, timeout=20)
                    for width, caption in ((720, "none"), (1360, "flex")):
                        page = browser.new_page(viewport={"width": width, "height": 820})
                        page.goto(servers.pg + "/")
                        page.wait_for_selector("body.boot-ready", timeout=90000)
                        page.wait_for_timeout(600)
                        state = page.evaluate("""() => { const c = document.getElementById('model-current');
                          return {text: c.textContent, scroll: c.scrollWidth, client: c.clientWidth,
                                  caption: getComputedStyle(document.getElementById('model-dd-btn'), '::before').display}; }""")
                        self.assertEqual(state["caption"], caption, (lang, width, state))
                        self.assertLessEqual(state["scroll"], state["client"] + 1, (lang, width, state))
                        page.close()
                browser.close()
        finally:
            servers.stop()


if __name__ == "__main__":
    unittest.main()
