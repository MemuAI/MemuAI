"""Render the real music module offline: edge coverage survives themes and enlargement."""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

def _chromium():
    candidates = [os.environ.get("MEMU_TEST_CHROMIUM", ""),
                  r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                  r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "/usr/bin/chromium"]
    return next((value for value in candidates if value and Path(value).is_file()), "")

@pytest.fixture(scope="module")
def music_browser():
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    if not _chromium():
        pytest.skip("no headless Chromium")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=_chromium(), headless=True,
                                     args=["--no-sandbox", "--disable-background-networking"])
        yield browser
        browser.close()

@pytest.fixture
def module_page(music_browser):
    from round37_music_harness import PARENT, frame_of, module_html, wait
    context = music_browser.new_context(viewport={"width": 1000, "height": 720}, device_scale_factor=1.25)
    page = context.new_page()
    page.route("**/*", lambda route: route.abort())
    page.route("http://harness.local/", lambda route: route.fulfill(status=200, content_type="text/html", body=PARENT))
    doc = module_html("ru", "dark")
    page.route("http://harness.local/module?mock=1", lambda route: route.fulfill(status=200, content_type="text/html", body=doc))
    page.goto("http://harness.local/")
    frame = frame_of(page)
    wait(page, frame, "() => !!(window.MemuMusic && MemuMusic.S.loaded)")
    frame.evaluate("""() => {
      const M = MemuMusic;
      M.Theme.set('motion', 'off');
      M.Theme.set('bg', 'cover');
      M.Player.playList(M.S.tracks.slice(0, 3), M.S.tracks[0].id, 'Offline test');
      M.NP().open();
    }""")
    yield page, frame
    context.close()

COVERAGE = """() => {
  const np = document.querySelector('#np'), image = document.querySelector('#np-blur');
  const n = np.getBoundingClientRect(), r = image.getBoundingClientRect();
  const b = parseFloat(getComputedStyle(image).filter.match(/blur\\(([^p]+)px/)[1]);
  return { width: innerWidth, height: innerHeight, layer: r.toJSON(), viewport: n.toJSON(), blur: b,
    maxWidth: getComputedStyle(image).maxWidth,
    covered: r.left <= n.left - b * 3 + 1 && r.top <= n.top - b * 3 + 1 && r.right >= n.right + b * 3 - 1 && r.bottom >= n.bottom + b * 3 - 1,
    hscroll: document.documentElement.scrollWidth > innerWidth + 1 };
}"""

@pytest.mark.parametrize("scheme,np_theme", [("dark", "dark"), ("light", "dark"), ("light", "auto")])
@pytest.mark.parametrize("style", ["card", "full"])
def test_background_covers_every_edge_before_and_after_enlargement(module_page, scheme, np_theme, style):
    page, frame = module_page
    frame.evaluate("""([scheme, theme, style]) => {
      MemuMusic.Theme.set('mode', scheme); MemuMusic.Theme.set('npTheme', theme); MemuMusic.Theme.set('npStyle', style);
    }""", [scheme, np_theme, style])
    for size in [{"width":760,"height":640}, {"width":1920,"height":540}, {"width":1360,"height":860}]:
        page.set_viewport_size(size)
        page.wait_for_timeout(80)
        result = frame.evaluate(COVERAGE)
        assert result["covered"], result
        assert result["viewport"]["x"] == result["viewport"]["y"] == 0, result
        assert result["viewport"]["width"] == result["width"], result
        assert result["viewport"]["height"] == result["height"], result
        assert not result["hscroll"], result

def test_coverage_check_catches_the_original_image_cap(module_page):
    page, frame = module_page
    assert frame.evaluate(COVERAGE)["covered"]
    frame.evaluate("""() => { const s = document.createElement('style'); s.id='old-edge-bug';
      s.textContent='.mm .mm-np-blur {max-width:100%;width:120%;height:120%;inset:-10%}'; document.head.append(s); }""")
    assert not frame.evaluate(COVERAGE)["covered"]
    frame.evaluate("() => document.querySelector('#old-edge-bug').remove()")
    assert frame.evaluate(COVERAGE)["covered"]

def test_dark_player_has_no_white_corner_pixels_on_light_host(module_page, tmp_path):
    from PIL import Image
    page, frame = module_page
    frame.evaluate("""() => { MemuMusic.Theme.set('mode','light'); MemuMusic.Theme.set('npTheme','dark'); }""")
    page.set_viewport_size({"width":1360,"height":860})
    page.wait_for_timeout(150)
    png = page.screenshot()
    (tmp_path / "dark-player-light-host.png").write_bytes(png)
    image = Image.open(io.BytesIO(png)).convert("RGB")
    w, h = image.size
    for x0, y0 in [(0,0), (w-8,0), (0,h-8), (w-8,h-8)]:
        values = [image.getpixel((x,y)) for x in range(x0,x0+8) for y in range(y0,y0+8)]
        assert all(min(pixel) < 220 for pixel in values), (x0,y0,values)

def test_background_coverage_survives_browser_scale(module_page):
    page, frame = module_page
    for zoom in ["1", "1.25", "1.5"]:
        frame.evaluate("zoom => document.documentElement.style.zoom=zoom", zoom)
        page.wait_for_timeout(60)
        result = frame.evaluate(COVERAGE)
        assert result["covered"], result
