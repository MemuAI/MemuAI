# -*- coding: utf-8 -*-
"""Browser harness for the MemuMusic module (Round 37).

Builds the real module document (real bridge from modules_host.BRIDGE_JS) and runs it in Chromium inside a
sandboxed iframe whose parent is a tiny mock of the MemuAI host. The module runs in its demo mode (?mock=1):
library, downloads and audio come from mock.js, so no Proxy and no network are needed.

Manual screenshots:  python3 tests/round37_music_harness.py /tmp/shots
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def chromium() -> str:
    for candidate in (os.environ.get("MEMU_TEST_CHROMIUM", ""), "/usr/bin/chromium", "/workspace/work/chromium/chrome",
                      shutil.which("chromium") or "", shutil.which("chromium-browser") or ""):
        if candidate and Path(candidate).exists():
            return candidate
    return ""


def module_html(lang: str = "ru", theme: str = "dark", saved: dict | None = None) -> str:
    from app.modules_host import load_package, module_document
    with tempfile.TemporaryDirectory() as temp:
        package = load_package(Path(temp), "music")
    return module_document(package, saved or {}, env={"lang": lang, "theme": theme})


PARENT = r"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>html,body{margin:0;height:100%;background:#000}
iframe{border:0;width:100%;height:100%;display:block}</style></head><body>
<iframe id="f" sandbox="allow-scripts" allow="autoplay" src="/module?mock=1"></iframe>
<script>
window.__saved = {}; window.__calls = [];
const frame = document.getElementById("f");
function reply(id, result, error) { frame.contentWindow.postMessage({ type: "memu-module-result", id, result, error: error || "" }, "*"); }
window.addEventListener("message", (e) => {
  const d = e.data || {};
  if (d.type !== "memu-module") return;
  window.__calls.push(d.action);
  const p = d.payload || {};
  if (d.action === "save") { Object.assign(window.__saved, p.values || p); return reply(d.id, window.__saved); }
  if (d.action === "open") return reply(d.id, { ok: true });
  return reply(d.id, { ok: true });
});
</script></body></html>"""


def open_page(pw, lang="ru", theme="dark", size=(1360, 860)):
    browser = pw.chromium.launch(executable_path=chromium(), args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"])
    page = browser.new_page(viewport={"width": size[0], "height": size[1]})
    doc = module_html(lang, theme)
    errors: list = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    page.route("http://harness.local/", lambda route: route.fulfill(status=200, content_type="text/html", body=PARENT))
    page.route("http://harness.local/module?mock=1", lambda route: route.fulfill(status=200, content_type="text/html", body=doc))
    page.goto("http://harness.local/")
    return browser, page, errors


def frame_of(page):
    for _ in range(100):
        for frame in page.frames:
            if frame.url.endswith("/module?mock=1"):
                return frame
        page.wait_for_timeout(50)
    raise RuntimeError("module frame not found")


def wait(page, frame, script: str, timeout: int = 15000) -> bool:
    """Poll in the frame (its CSP forbids the eval behind wait_for_function)."""
    for _ in range(timeout // 100):
        if frame.evaluate(script):
            return True
        page.wait_for_timeout(100)
    raise TimeoutError(script)


def shots(out: str) -> None:
    from playwright.sync_api import sync_playwright
    os.makedirs(out, exist_ok=True)
    with sync_playwright() as pw:
        for theme, size, tag in (("dark", (1360, 860), "dark"), ("light", (1360, 860), "light"), ("dark", (760, 640), "narrow")):
            browser, page, errors = open_page(pw, "ru", theme, size)
            frame = frame_of(page)
            wait(page, frame, "() => !!(window.MemuMusic && MemuMusic.S.loaded)")
            page.wait_for_timeout(900)
            views = ["home", "library", "playlists", "download", "stats", "settings"] if tag != "narrow" else ["home", "library", "download"]
            for view in views:
                frame.evaluate(f"() => MemuMusic.go('{view}', {{}}, {{instant: true}})")
                if view == "download":
                    frame.evaluate("() => { const i = document.querySelector('#dl-url'); i.value = 'https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy'; i.dispatchEvent(new Event('input')); }")
                    frame.evaluate("() => MemuMusic.API.call('download.start', {url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'})")
                    frame.evaluate("() => MemuMusic.API.call('download.start', {url: 'https://music.yandex.ru/album/123'})")
                    frame.evaluate("() => MemuMusic.pollJobs()")
                    page.wait_for_timeout(2600)
                page.wait_for_timeout(1400)
                page.screenshot(path=f"{out}/{tag}-{view}.png")
            frame.evaluate("() => { const S = MemuMusic.S; MemuMusic.Player.playList(S.tracks.slice(0, 20), S.tracks[1].id, 'Демо'); }")
            page.wait_for_timeout(2500)
            frame.evaluate("() => MemuMusic.NP().open()")
            page.wait_for_timeout(2600)
            page.screenshot(path=f"{out}/{tag}-nowplaying.png")
            if tag == "dark":
                frame.evaluate("() => document.querySelector('#np-style [data-style=vinyl]').click()")
                frame.evaluate("() => document.querySelector('.mm-np-tabs [data-tab=queue]').click()")
                page.wait_for_timeout(1500)
                page.screenshot(path=f"{out}/{tag}-nowplaying-vinyl.png")
                frame.evaluate("() => { document.querySelector('#np-style [data-style=card]').click(); document.querySelector('[data-pop=eq]').click(); }")
                page.wait_for_timeout(700)
                page.screenshot(path=f"{out}/{tag}-eq.png")
            print(tag, "errors:", errors[:5])
            browser.close()


if __name__ == "__main__":
    shots(sys.argv[1] if len(sys.argv) > 1 else "/tmp/music-shots")
