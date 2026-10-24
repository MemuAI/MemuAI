"""Round 29 wave F: Quick Access — Downloads folder lookup, Downloads PNG attached as an image, Explorer answer
pinned to the end when the window/cards resize."""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import quick_apps, quick_extras

ROOT = Path(__file__).resolve().parents[1]
QUICK = ROOT / "app" / "pgstatic" / "quick"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def _clean_env():
    return mock.patch.dict(os.environ, {"XDG_DOWNLOAD_DIR": "", "XDG_CONFIG_HOME": ""})


class DownloadsFolder(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix="r29f-home-"))
        self.addCleanup(shutil.rmtree, self.home, True)

    def _home(self):
        return mock.patch("pathlib.Path.home", return_value=self.home)

    def test_no_folder_lists_nothing_not_the_working_folder(self) -> None:
        # Path("") is "." — the list used to show MemuAI's own folder (spec files, README) when Downloads is missing.
        cwd = os.getcwd()
        os.chdir(ROOT)
        try:
            with self._home(), _clean_env(), mock.patch.object(quick_apps, "IS_WINDOWS", False):
                self.assertEqual(quick_apps.downloads_folder(), "")
                self.assertEqual(quick_extras.latest_downloads(), [])
                self.assertEqual(quick_extras.latest_downloads("."), [])
                self.assertEqual(quick_extras.latest_downloads("app"), [])
                self.assertIn("error", quick_extras.downloads_context("README.md"))
                app = FastAPI()
                app.include_router(quick_extras.router)
                body = TestClient(app).get("/api/quick/extras/downloads").json()
                self.assertEqual(body, {"items": [], "folder": "", "found": False})
        finally:
            os.chdir(cwd)

    def test_home_downloads_and_route(self) -> None:
        (self.home / "Downloads").mkdir()
        (self.home / "Downloads" / "a.txt").write_text("x", encoding="utf-8")
        with self._home(), _clean_env(), mock.patch.object(quick_apps, "IS_WINDOWS", False):
            self.assertEqual(quick_apps.downloads_folder(), str(self.home / "Downloads"))
            app = FastAPI()
            app.include_router(quick_extras.router)
            body = TestClient(app).get("/api/quick/extras/downloads").json()
            self.assertEqual([r["name"] for r in body["items"]], ["a.txt"])
            self.assertEqual((body["folder"], body["found"]), ("Downloads", True))

    def test_xdg_user_dirs(self) -> None:
        local = self.home / "Загрузки"
        local.mkdir()
        (self.home / "Downloads").mkdir()          # the localised XDG folder wins over a stray ~/Downloads
        conf = self.home / ".config"
        conf.mkdir()
        (conf / "user-dirs.dirs").write_text('# comment\nXDG_DESKTOP_DIR="$HOME/Рабочий стол"\nXDG_DOWNLOAD_DIR="$HOME/Загрузки"\n',
                                             encoding="utf-8")
        with self._home(), _clean_env(), mock.patch.object(quick_apps, "IS_WINDOWS", False):
            self.assertEqual(quick_apps.downloads_folder(), str(local))
            # disabled (= $HOME) or missing → fall back to ~/Downloads
            (conf / "user-dirs.dirs").write_text('XDG_DOWNLOAD_DIR="$HOME/"\n', encoding="utf-8")
            self.assertEqual(quick_apps.downloads_folder(), str(self.home / "Downloads"))
            (conf / "user-dirs.dirs").write_text('XDG_DOWNLOAD_DIR="$HOME/nope"\n', encoding="utf-8")
            self.assertEqual(quick_apps.downloads_folder(), str(self.home / "Downloads"))
        other = Path(tempfile.mkdtemp(prefix="r29f-dl-"))
        self.addCleanup(shutil.rmtree, other, True)
        with self._home(), mock.patch.dict(os.environ, {"XDG_DOWNLOAD_DIR": str(other)}), \
                mock.patch.object(quick_apps, "IS_WINDOWS", False):
            self.assertEqual(quick_apps.downloads_folder(), str(other))

    def test_windows_known_folder_then_profile(self) -> None:
        from app import quick_apps_win
        known = Path(tempfile.mkdtemp(prefix="r29f-known-"))
        self.addCleanup(shutil.rmtree, known, True)
        profile = Path(tempfile.mkdtemp(prefix="r29f-profile-"))
        self.addCleanup(shutil.rmtree, profile, True)
        (profile / "Downloads").mkdir()
        with self._home(), mock.patch.object(quick_apps, "IS_WINDOWS", True), \
                mock.patch.dict(os.environ, {"USERPROFILE": str(profile)}):
            with mock.patch.object(quick_apps_win, "known_folder", return_value=str(known)):
                self.assertEqual(quick_apps.downloads_folder(), str(known))
            # the shell API failed or names a folder that is gone (redirected to an unplugged drive)
            for value in ("", str(known / "gone")):
                with mock.patch.object(quick_apps_win, "known_folder", return_value=value):
                    self.assertEqual(quick_apps.downloads_folder(), str(profile / "Downloads"))


class DownloadImage(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="r29f-dlimg-"))
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.patch = mock.patch.object(quick_apps, "downloads_folder", return_value=str(self.folder))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_png_is_an_image_data_url(self) -> None:
        (self.folder / "receipt.PNG").write_bytes(PNG)
        out = quick_extras.downloads_context("receipt.PNG", "en")
        self.assertTrue(out["image"].startswith("data:image/png;base64,"))
        self.assertEqual(base64.b64decode(out["image"].split(",", 1)[1]), PNG)
        self.assertEqual(out["context"]["selected"], ["receipt.PNG"])

    def test_txt_and_big_png_stay_a_file_context(self) -> None:
        (self.folder / "notes.txt").write_text("hi", encoding="utf-8")
        out = quick_extras.downloads_context("notes.txt", "en")
        self.assertNotIn("image", out)
        self.assertIn("context", out)
        big = self.folder / "huge.png"
        with big.open("wb") as f:
            f.truncate(quick_extras.DOWNLOAD_IMAGE_LIMIT + 1)
        out = quick_extras.downloads_context("huge.png", "en")
        self.assertNotIn("image", out)
        self.assertIn("12 MB", out["image_error"])
        self.assertIn("context", out)
        self.assertNotIn("image", quick_extras.downloads_context("empty.png", "en"))   # not found → error

    def test_frontend_attaches_it_as_an_image(self) -> None:
        js = (QUICK / "plugins" / "extras.js").read_text(encoding="utf-8")
        self.assertIn("r.image && Q.addImage && Q.addImage({ data: r.image", js)
        self.assertIn('d.found !== false', js)
        self.assertIn("The Downloads folder was not found", js)
        quick = (QUICK / "quick.js").read_text(encoding="utf-8")
        self.assertIn("addImage", quick)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class AnswerPinOnResize(unittest.TestCase):
    """quick.js pinAnswerOnResize: when the cards under the answer grow, the answer box shrinks; if the reader was at the
    end it must stay at the end, if they scrolled up to re-read it must not jump."""

    def test_pin(self) -> None:
        src = (QUICK / "quick.js").read_text(encoding="utf-8")
        start = src.index("const answerFollow = {")   # Round 30: following is the reader's choice (user scrolls only)
        end = src.index("})();", start) + len("})();")
        script = """
const listeners = {}; let observer = null;
const box = { hidden: false, scrollHeight: 1000, clientHeight: 300, scrollTop: 700,
  addEventListener(type, fn) { listeners[type] = fn; } };
global.ResizeObserver = class { constructor(cb) { this.cb = cb; observer = this; } observe(el) { this.el = el; } unobserve() {} };
const $ = (id) => id === "qa-answer" ? box : null;
let clock = 0; const performance = { now: () => clock };   // the «a user scrolled» window is 700 ms
""" + src[start:end] + """
const out = [];
const userScroll = (top) => { listeners.wheel(); box.scrollTop = top; listeners.scroll(); };
if (!observer || observer.el !== box) throw new Error("no observer");
box.clientHeight = 100; observer.cb(); out.push(box.scrollTop);          // following by default -> pinned to 1000
userScroll(200);                                       // reader scrolled up
box.clientHeight = 50; observer.cb(); out.push(box.scrollTop);           // must not jump
clock += 1000; box.scrollTop = 940; listeners.scroll();   // a programmatic scroll does not turn following back on
box.clientHeight = 45; observer.cb(); out.push(box.scrollTop);
clock += 1000; userScroll(1000 - 45 - 30);                           // within 40 px of the end counts as the end
box.clientHeight = 20; observer.cb(); out.push(box.scrollTop);
box.hidden = true; box.scrollTop = 5; observer.cb(); out.push(box.scrollTop);   // hidden box untouched
console.log(JSON.stringify(out));
"""
        res = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads(res.stdout.strip()), [1000, 200, 940, 1000, 5])


class MiniAppWideLayout(unittest.TestCase):
    """r29f Mini App EN sweep (tablet landscape / desktop / phone): the split list was narrower than a phone (340 px) and
    cut English settings rows; short row values sat away from the chevron; empty-pane hints left a lone last word."""
    TG = ROOT / "app" / "tgapp"

    def test_split_list_width(self) -> None:
        layout = (self.TG / "layout.css").read_text(encoding="utf-8")
        device = (self.TG / "device.js").read_text(encoding="utf-8")
        self.assertIn('html[data-layout="split"] { --list-w: 360px; }', layout)
        self.assertIn("--lw: clamp(250px, var(--list-w), 46vw)", layout)      # still capped on small tablets
        self.assertIn("DEFAULT_LIST = 360", device)
        # double-click resets to the CSS width (not a hard-coded inline one)
        self.assertIn('addEventListener("dblclick", () => { root.style.removeProperty("--list-w")', device)

    def test_values_and_hints(self) -> None:
        css = (self.TG / "app.css").read_text(encoding="utf-8")
        rule = next(line for line in css.splitlines() if line.startswith(".cell-value {"))
        self.assertIn("text-align: right", rule)
        layout = (self.TG / "layout.css").read_text(encoding="utf-8")
        self.assertRegex(layout, r"\.detail-empty p \{[^}]*text-wrap: balance")
        html = (self.TG / "index.html").read_text(encoding="utf-8")
        # the cache-busting versions moved past r29 (r30 bumped them again): only require "at least the r29 bump"
        for asset in ("app.css", "layout.css", "device.js", "app.js"):
            m = re.search(r'"%s\?v=\d+\.\d+-r(\d+)"' % re.escape(asset), html)
            self.assertIsNotNone(m, asset)
            self.assertGreaterEqual(int(m.group(1)), 29, asset)
        self.assertIn('"Images for non-vision models"', (self.TG / "app.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
