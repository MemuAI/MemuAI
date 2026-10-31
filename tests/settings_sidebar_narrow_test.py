# -*- coding: utf-8 -*-
"""Narrow sidebar: at a window width of 800px the sidebar is an off-canvas
drawer, so its footer ("Settings and profile") and the module launchers
start outside the viewport and a direct mouse click on them cannot land.

Static contracts run everywhere. The browser part runs the REAL drawer
block from ``app/pgstatic/app.js`` (sliced out verbatim) in headless
Chromium with the real sidebar CSS and checks coordinates, not eyeballs:

- at 800x600 the drawer starts open: the profile card and the module
  launcher are inside the viewport;
- dismissing via a main-area click and via the hamburger still works and
  the drawer stays closed (the hidden-state observer must not re-open it);
- hiding the sidebar removes the drawer; un-hiding restores it;
- at 1400px the block changes nothing: no forced ``open`` class.

Skipped without a Chromium binary (MEMU_TEST_CHROMIUM may point at one).
The final live check in the real app belongs to the browser agent.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PG = ROOT / "app" / "pgstatic"
sys.path.insert(0, str(ROOT))

APP_JS = (PG / "app.js").read_text(encoding="utf-8")


def _chromium() -> str:
    candidates = [
        os.environ.get("MEMU_TEST_CHROMIUM", ""),
        "/usr/bin/chromium",
        shutil.which("chromium") or "",
        shutil.which("chromium-browser") or "",
        "/workspace/work/chromium/chrome",
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    # tests/__init__.py isolates HOME on purpose, so "~" does not point at
    # the real profile cache here; resolve the real home from the passwd
    # entry (and JARVIS_HOME) as well.
    homes = {os.path.expanduser("~"), os.environ.get("JARVIS_HOME", "")}
    try:
        import pwd

        homes.add(pwd.getpwuid(os.getuid()).pw_dir)
    except Exception:
        pass
    for home in homes:
        if not home:
            continue
        for candidate in glob.glob(
            os.path.join(home, ".cache", "ms-playwright", "**", "chrome-headless-shell"),
            recursive=True,
        ):
            if os.path.exists(candidate):
                return candidate
    return ""


def _drawer_block() -> str:
    start = APP_JS.index('$("#mobile-menu").onclick')
    end = APP_JS.index("function currentModel()")
    return APP_JS[start:end]


def _sidebar_css() -> str:
    return "\n".join(
        (PG / name).read_text(encoding="utf-8")
        for name in ("style.css", "appearance.css", "design.css", "appearance-layout.css")
    )


HARNESS = """<!doctype html><html><head><meta charset="utf-8"><style>__CSS__</style></head><body>
<div id="app"><aside class="side"><div class="side-top"><div class="side-scroll"><div id="module-slot-sidebar"><button class="side-btn" id="launch-meet">MemuMeet</button></div></div></div><div class="side-bottom"><button class="profile-card" id="profile-card"><span class="profile-info"><span class="profile-sub">Настройки и профиль</span></span></button></div></aside><main class="main"><button id="mobile-menu">☰</button><p>main</p></main></div>
<pre id="out">PENDING</pre>
<script>
const $=(s,n)=>(n||document).querySelector(s);
const PREFS={sidebar_visible:true};
function pref(k,fb){return PREFS[k]!==undefined?PREFS[k]:fb;}
</script>
<script>__BLOCK__</script>
<script>
(async()=>{
 const sleep=ms=>new Promise(r=>setTimeout(r,ms));
 const out=[]; const side=document.querySelector('.side');
 const rect=el=>{const r=el.getBoundingClientRect();return {x:Math.round(r.x),bottom:Math.round(r.bottom)};};
 const snap=async(label)=>{await sleep(30);out.push({label,cls:side.className,profile:rect(document.getElementById('profile-card')),meet:rect(document.getElementById('launch-meet')),inVP:(()=>{const r=document.getElementById('profile-card').getBoundingClientRect();return r.left>=0&&r.bottom<=innerHeight;})()});};
 await snap('initial');
 document.querySelector('.main').click(); await snap('after-main-click-dismiss');
 document.getElementById('mobile-menu').click(); await snap('after-hamburger-open');
 document.getElementById('mobile-menu').click(); await snap('after-hamburger-close');
 side.classList.add('hidden'); await snap('after-hidden-added');
 side.classList.remove('hidden'); await snap('after-hidden-removed');
 PREFS.sidebar_visible=false; side.classList.add('hidden'); await snap('pref-false-hidden');
 document.getElementById('out').textContent=JSON.stringify(out,null,1);
})();
</script></body></html>"""


def _run_harness(width: int, height: int) -> list:
    chromium = _chromium()
    if not chromium:
        raise unittest.SkipTest("no Chromium binary (set MEMU_TEST_CHROMIUM)")
    html = HARNESS.replace("__CSS__", _sidebar_css()).replace(
        "__BLOCK__", _drawer_block().replace("</script>", "<\\/script>")
    )
    with tempfile.TemporaryDirectory(prefix="memu-narrow-sidebar-") as tmp:
        page = Path(tmp) / "harness.html"
        page.write_text(html, encoding="utf-8")
        proc = subprocess.run(
            [
                chromium,
                "--headless",
                "--disable-gpu",
                "--no-sandbox",
                "--allow-file-access-from-files",
                f"--window-size={width},{height}",
                "--virtual-time-budget=5000",
                "--dump-dom",
                page.as_uri(),
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
    match = re.search(r'<pre id="out">(.*?)</pre>', proc.stdout, re.S)
    assert match, f"harness produced no output (rc={proc.returncode}, stderr={proc.stderr[:300]})"
    return json.loads(match.group(1))


class StaticContractTest(unittest.TestCase):
    def test_narrow_sidebar_sync_exists(self):
        block = _drawer_block()
        self.assertIn("syncNarrowSidebar", block)
        self.assertIn("window.innerWidth > 800", block)
        self.assertIn('side.classList.contains("hidden")', block)
        self.assertIn('pref("sidebar_visible", true)', block)
        # The observer must react only to hidden-state changes, otherwise
        # closing the drawer with the hamburger would re-open it.
        self.assertIn("narrowSideHidden", block)
        self.assertIn("hidden === narrowSideHidden", block)

    def test_drawer_css_still_hides_sidebar_by_default(self):
        css = (PG / "appearance.css").read_text(encoding="utf-8")
        self.assertIn("transform: translateX(-105%)", css)
        self.assertIn(".side.open, .side.hidden.open { transform: translateX(0); }", css)


class BrowserSidebarTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.narrow = {s["label"]: s for s in _run_harness(800, 600)}
        cls.wide = {s["label"]: s for s in _run_harness(1400, 900)}

    def test_footer_and_module_launcher_reachable_at_800px(self):
        initial = self.narrow["initial"]
        self.assertIn("open", initial["cls"].split())
        self.assertTrue(initial["inVP"])
        self.assertGreaterEqual(initial["profile"]["x"], 0)
        self.assertGreaterEqual(initial["meet"]["x"], 0)

    def test_dismiss_gestures_still_work_at_800px(self):
        self.assertNotIn("open", self.narrow["after-main-click-dismiss"]["cls"].split())
        self.assertIn("open", self.narrow["after-hamburger-open"]["cls"].split())
        self.assertNotIn("open", self.narrow["after-hamburger-close"]["cls"].split())

    def test_hidden_state_sync_at_800px(self):
        self.assertNotIn("open", self.narrow["after-hidden-added"]["cls"].split())
        self.assertIn("open", self.narrow["after-hidden-removed"]["cls"].split())
        self.assertNotIn("open", self.narrow["pref-false-hidden"]["cls"].split())

    def test_wide_layout_untouched(self):
        self.assertNotIn("open", self.wide["initial"]["cls"].split())
        self.assertNotIn("open", self.wide["after-hidden-removed"]["cls"].split())
        self.assertIn("open", self.wide["after-hamburger-open"]["cls"].split())


if __name__ == "__main__":
    unittest.main()
