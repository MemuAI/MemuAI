# -*- coding: utf-8 -*-
"""Round 38 — MemuMusic design: themes, designs, appearance settings, cover sizing, no layout jumps.

Static checks always run; the headless part runs the real module document in Chromium (demo mode, no network)
and is skipped when no Chromium is available (set MEMU_TEST_CHROMIUM)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MUSIC = ROOT / "app" / "module_builtins" / "music"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

def read(name: str) -> str:
    return (MUSIC / name).read_text(encoding="utf-8")

def test_theme_part_is_inlined_after_art():
    main = read("main.js")
    assert main.index("/* @memu-part:art.js */") < main.index("/* @memu-part:theme.js */") < main.index("/* @memu-part:views.js */")
    assert "const SETTINGS_TABS = [];" in main and main.index("const SETTINGS_TABS") < main.index("/* @memu-part:theme.js */")
    theme = read("theme.js")
    assert 'SETTINGS_TABS.push({ id: "appearance"' in theme

def test_catalogue_has_enough_themes_and_designs():
    theme = read("theme.js")
    ids = re.findall(r'\{ id: "([a-z]+)", h:', theme)
    assert len(ids) >= 11 and {"cover", "midnight", "aurora", "sunset", "ocean", "neon", "sakura", "moss", "sand", "graphite", "vinyl", "memu"} <= set(ids)
    assert 'const DESIGNS = ["glass", "minimal", "retro", "neon", "compact"]' in theme
    for mode in ("light", "dark", "system", "memu"):
        assert f'"{mode}"' in theme
    css = read("style.css")
    for design in ("minimal", "retro", "neon", "compact"):
        assert f'html[data-mm-design="{design}"]' in css

def test_i18n_has_every_appearance_key_in_both_languages():
    data = json.loads(read("i18n.json"))
    ru = {k for k in data["ru"] if k.startswith("ui.")}
    en = {k for k in data["en"] if k.startswith("ui.")}
    assert ru == en and len(ru) > 80
    theme = read("theme.js")
    for key in re.findall(r'L\("([a-z_.]+)"', theme):
        if "." in key and not key.endswith("."):
            assert "ui." + key in ru, key
    for th in re.findall(r'\{ id: "([a-z]+)", h:', theme):
        assert f"ui.theme.{th}" in ru and f"ui.theme.{th}_hint" in ru

def test_css_bugs_of_round37_are_gone():
    css = read("style.css")
    assert 'html[data-theme="light"]' not in css, "light scheme must follow the MemuMusic mode, not only the host"
    assert "grid-auto-columns: var(--mm-tile)" in css, "shelves must have fixed-width tiles (1fr stretched 1–2 covers to the whole screen)"
    assert "grid-auto-columns: minmax(168px, 1fr)" not in css
    assert "::view-transition-new(root)" not in css, "the whole-window view transition moved sidebar and player bar"
    assert ".mm-lyrics-orb" not in css, "the empty lyrics screen had a clipped ring"
    assert "backdrop-filter" not in css, "no backdrop blur in the module (performance rule)"
    assert "startViewTransition" not in read("main.js")
    assert ".mm-np-tabs-pill" in css and 'data-len="xl"' in css

def test_np_markup_has_four_styles_and_tab_pill():
    html = read("index.html")
    for style in ("card", "vinyl", "full", "minimal"):
        assert f'data-style="{style}"' in html
    assert "mm-np-tabs-pill" in html and 'id="np-blur"' in html and 'id="np-backdrop"' in html
    assert 'class="mm-dragzone" data-memu-drag' in html
    assert "setMode(m)" in read("fx.js")

def _chromium() -> str:
    from round37_music_harness import chromium
    return chromium()

needs_browser = pytest.mark.skipif(not _chromium(), reason="no Chromium")

@pytest.fixture(scope="module")
def page_frame():
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright
    from round37_music_harness import frame_of, open_page, wait
    from round38_music_design_audit import cover_images, inject_covers
    with sync_playwright() as pw:
        browser, page, errors = open_page(pw, "ru", "dark", (1360, 860))
        frame = frame_of(page)
        wait(page, frame, "() => !!(window.MemuMusic && MemuMusic.S.loaded && MemuMusic.Theme)")
        inject_covers(frame, cover_images(), few=True)
        frame.evaluate("() => MemuMusic.Theme.set('motion', 'off')")
        yield page, frame, errors
        browser.close()

@needs_browser
def test_real_covers_never_take_the_screen(page_frame):
    from round38_music_design_audit import AUDIT_JS
    page, frame, errors = page_frame
    frame.evaluate("() => MemuMusic.go('home', {}, { instant: true })")
    page.wait_for_timeout(500)
    sizes = frame.evaluate("() => [...document.querySelectorAll('#view img')].filter((i) => !i.closest('.mm-flow')).map((i) => { const r = i.getBoundingClientRect(); return [r.width, r.height]; })")
    assert sizes and max(max(w, h) for w, h in sizes) <= 260
    assert all(abs(w - h) < 2 for w, h in sizes), "every cover is a centre-cropped square"
    found = [f for f in frame.evaluate(AUDIT_JS, {"layer": ""}) if f["kind"].startswith("image") or f["kind"] == "hscroll"]
    assert not found, found
    assert not errors, errors

@needs_browser
def test_auditor_catches_a_planted_bug(page_frame):
    from round38_music_design_audit import AUDIT_JS
    page, frame, _ = page_frame
    frame.evaluate("() => { const s = document.createElement('div'); s.id = 'planted'; s.style.cssText = 'width:900px'; s.innerHTML = '<img src=\"' + MemuMusic.coverArt('x') + '\" style=\"width:900px;height:500px\">'; document.querySelector('#view').append(s); }")
    kinds = {f["kind"] for f in frame.evaluate(AUDIT_JS, {"layer": ""})}
    frame.evaluate("() => document.querySelector('#planted').remove()")
    assert "image-huge" in kinds and "image-ratio" in kinds

@needs_browser
def test_every_theme_is_readable_in_both_schemes(page_frame):
    page, frame, _ = page_frame
    rows = frame.evaluate("""() => { const T = MemuMusic.Theme, out = [], cs = () => getComputedStyle(document.documentElement);
      for (const mode of ['dark', 'light']) for (const th of T.THEMES) {
        T.ui.theme = th.id; T.set('mode', mode);
        const bg = cs().getPropertyValue('--mm-bg').trim(), text = cs().getPropertyValue('--mm-text').trim(), ink = cs().getPropertyValue('--tint-ink').trim();
        const solid = cs().getPropertyValue('--tint-solid').trim(), on = cs().getPropertyValue('--mm-on-tint').trim();
        out.push({ mode, th: th.id, scheme: document.documentElement.dataset.mmScheme, text: T.contrast(text, bg), ink: T.contrast(ink, bg), button: T.contrast(on, solid) });
      } T.ui.theme = 'cover'; T.set('mode', 'dark'); return out; }""")
    assert len(rows) >= 24
    for r in rows:
        assert r["scheme"] == r["mode"], r
        assert r["text"] >= 7, r
        assert r["ink"] >= 4.5, r
        assert r["button"] >= 4.5, r

@needs_browser
def test_switching_sections_does_not_shift_layout(page_frame):
    from round38_music_design_audit import CLS_INSTALL
    page, frame, _ = page_frame
    frame.evaluate("() => MemuMusic.Theme.set('motion', 'full')")
    frame.evaluate("() => MemuMusic.go('home', {}, { instant: true })")
    page.wait_for_timeout(400)
    frame.evaluate(CLS_INSTALL)
    side = frame.evaluate("() => JSON.stringify(document.querySelector('#side').getBoundingClientRect())")
    for view in ("library", "home", "stats", "playlists", "home"):
        frame.evaluate(f"() => MemuMusic.go('{view}')")
        page.wait_for_timeout(350)
        assert frame.evaluate("() => JSON.stringify(document.querySelector('#side').getBoundingClientRect())") == side
    assert frame.evaluate("() => window.__cls") < 0.01
    frame.evaluate("() => MemuMusic.Theme.set('motion', 'off')")

@needs_browser
def test_appearance_tab_applies_instantly(page_frame):
    page, frame, _ = page_frame
    frame.evaluate("() => MemuMusic.go('settings', { tab: 'appearance' }, { instant: true })")
    page.wait_for_timeout(700)
    assert frame.evaluate("() => document.querySelectorAll('.mm-ap-themes .mm-ap-card').length") >= 12
    got = frame.evaluate("""() => { document.querySelector('[data-ui=theme] [data-v=ocean]').click(); document.querySelector('[data-ui=design] [data-v=retro]').click();
      document.querySelector('[data-ui=mode] [data-v=light]').click(); document.querySelector('[data-ui=density] [data-v=compact]').click();
      const d = document.documentElement.dataset; return [d.mmTheme, d.mmDesign, d.mmScheme, d.mmDensity, MemuMusic.S.prefs.ui.font,
        document.querySelector('[data-ui=theme] [data-v=ocean]').getAttribute('aria-checked')]; }""")
    assert got == ["ocean", "retro", "light", "compact", "serif", "true"]
    frame.evaluate("() => document.querySelector('#ap-reset').click()")
    assert frame.evaluate("() => document.documentElement.dataset.mmDesign") == "glass"

@needs_browser
def test_now_playing_styles_and_long_title(page_frame):
    page, frame, _ = page_frame
    frame.evaluate("() => { const S = MemuMusic.S; MemuMusic.Player.playList(S.tracks.slice(0, 3), S.tracks[0].id, 'Demo'); MemuMusic.NP().open(); }")
    page.wait_for_timeout(600)
    assert frame.evaluate("() => document.querySelector('#np-title').dataset.len") == "xl"
    for style in ("card", "vinyl", "full", "minimal"):
        frame.evaluate(f"() => MemuMusic.Theme.set('npStyle', '{style}')")
        page.wait_for_timeout(250)
        box = frame.evaluate("""() => { const t = document.querySelector('.mm-np-tabs').getBoundingClientRect(), c = document.querySelector('.mm-np-controls').getBoundingClientRect(),
          h = document.querySelector('#np-title'); return { style: document.querySelector('#np').dataset.style, gap: c.top - t.bottom, lines: Math.round(h.getBoundingClientRect().height / parseFloat(getComputedStyle(h).lineHeight)) }; }""")
        assert box["style"] == style and box["gap"] > 0 and box["lines"] <= 2, box
    frame.evaluate("() => MemuMusic.NP().close()")
