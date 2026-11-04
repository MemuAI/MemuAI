"""MemuFiles appearance: MemuNote's palettes, MemuAI's accents plus a custom colour, window designs and folder
colours — the schema, the stored values (old accent names, custom colours), the resolved tokens, the page boot
attributes, the picker in Settings and the «Look» menu. No network."""
import json
import os
import re
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "memufiles" / "static"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMUAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MEMUFILES_TRASH_DIR", str(tmp_path / "trash"))
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    yield


def _chromium():
    exe = os.environ.get("MEMU_TEST_CHROMIUM", "/workspace/work/chromium/chrome")
    if not os.path.exists(exe):
        pytest.skip("no chromium")
    return exe


def _prefs(tmp_path, **prefs):
    path = tmp_path / "data" / "memuai" / "preferences.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(prefs), encoding="utf-8")


def call(app, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{app.port}{path}", data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-MF-Token": app.token, "Content-Type": "application/json"},
                                 method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=10) as res:
        raw = res.read()
    return json.loads(raw) if raw[:1] == b"{" else raw


def test_schema_lists_every_choice():
    from app.memufiles import appearance as ap, settings_schema as sch
    items = {i["key"]: i for i in sch.public()["items"]}
    for key in ("palette", "accent", "accent_custom", "design", "folder_color"):
        assert items[key]["group"] == "appearance", key
    assert [c["value"] for c in items["palette"]["choices"]] == list(ap.PALETTE_IDS)
    assert [c["value"] for c in items["accent"]["choices"]] == list(ap.ACCENT_CHOICES)
    assert [c["value"] for c in items["design"]["choices"]] == list(ap.DESIGN_IDS)
    assert [c["value"] for c in items["folder_color"]["choices"]] == list(ap.FOLDER_IDS)
    assert len(ap.PALETTE_IDS) >= 19 and {"github", "gruvbox", "sepia", "tokyo"} <= set(ap.PALETTE_IDS)
    assert {"auto", "palette", "custom", "terracotta", "forest"} <= set(ap.ACCENT_CHOICES)
    assert ap.DESIGN_IDS == ("auto", "classic", "soft", "paper", "glass", "studio", "grid", "zen", "brutal")
    assert items["accent_custom"]["when"] == "accent=custom"
    # every choice has both languages and nothing old is left
    for key in ("palette", "accent", "design", "folder_color"):
        assert all(c["ru"] and c["en"] for c in items[key]["choices"]), key
    assert not {"pink", "red", "orange", "green"} & set(ap.ACCENT_CHOICES)
    # the custom colour is checked before MemuAI writes it
    assert sch.check("accent_custom", "#3355ff") == (True, "#3355ff", "")
    assert sch.check("accent_custom", "")[0] is True
    assert sch.check("accent_custom", "blue")[2] == "pattern"
    assert sch.check("accent", "chartreuse")[2] == "choice"
    assert sch.check("design", "zen")[0] and sch.check("design", "material")[2] == "choice"


def test_settings_migrate_old_accents_and_clean_custom_colour(tmp_path):
    from app.memufiles import settings
    path = tmp_path / "data" / "memufiles" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"accent": "green", "accent_custom": "ABC", "palette": "nope", "design": "glass",
                                "folder_color": "pink"}), encoding="utf-8")
    got = settings.load()
    assert got["accent"] == "forest" and got["accent_custom"] == "#aabbcc"
    assert got["palette"] == "auto" and got["design"] == "glass" and got["folder_color"] == "classic"
    for old, new in (("pink", "rose"), ("red", "crimson"), ("orange", "amber")):
        assert settings.save({"accent": old})["accent"] == new
    saved = settings.save({"accent": "custom", "accent_custom": " #3355FF ", "palette": "nord", "folder_color": "blue"})
    assert saved["accent"] == "custom" and saved["accent_custom"] == "#3355ff"
    assert saved["palette"] == "nord" and saved["folder_color"] == "blue"
    assert settings.save({"accent_custom": "not a colour"})["accent_custom"] == ""
    assert settings.save({"design": "material"})["design"] == "glass"  # unknown values keep the old one


def test_resolve_follows_memuai_and_fits_chosen_colours():
    from app.memufiles import appearance as ap
    from app.memunote import appearance as note
    from app import themes
    prefs = {"theme": "light", "accent_color": "ocean", "ui_design": "aurora"}
    # everything on «auto»: MemuAI's own tokens, mode and the closest design
    look = ap.resolve({"theme": "auto", "palette": "auto", "accent": "auto", "design": "auto"}, prefs)
    assert look["mode"] == "light" and look["design"] == "soft" and look["design_setting"] == "auto"
    assert look["tokens"] == dict(themes.resolve_tokens("light", "", "ocean"))
    assert ap.design_for("auto", "notebook") == "paper" and ap.design_for("auto", "terminal") == "grid"
    assert ap.design_for("auto", "unknown") == "classic" and ap.design_for("zen", "aurora") == "zen"
    # a palette of its own: the palette's surfaces, MemuAI's accent fitted to them
    for mode in ("dark", "light"):
        for pid in ap.PALETTE_IDS[1:]:
            look = ap.resolve({"theme": mode, "palette": pid, "accent": "auto"}, prefs)
            t = look["tokens"]
            assert t["--bg"] == note.palette_tokens(mode, pid)["--bg"], (mode, pid)
            assert note.contrast(t["--accent"], t["--bg"]) >= 4.5, (mode, pid, t["--accent"])
            assert note.contrast(t["--on-accent"], t["--accent"]) >= 4.5, (mode, pid)
    # a custom colour that would vanish on the background is fitted, not taken as is
    look = ap.resolve({"theme": "dark", "palette": "contrast", "accent": "custom", "accent_custom": "#101010"}, prefs)
    assert look["accent"] == "custom" and note.contrast(look["tokens"]["--accent"], look["tokens"]["--bg"]) >= 4.5
    look = ap.resolve({"theme": "light", "palette": "auto", "accent": "forest"}, prefs)
    assert look["tokens"]["--bg"] == themes.resolve_tokens("light", "", "ocean")["--bg"]
    assert look["tokens"]["--accent"] != themes.resolve_tokens("light", "", "ocean")["--accent"]
    look = ap.resolve({"theme": "light", "palette": "gruvbox", "accent": "palette"}, prefs)
    assert note.contrast(look["tokens"]["--accent"], look["tokens"]["--bg"]) >= 4.5
    assert ap.resolve({"folder_color": "mono"}, prefs)["folders"] == "mono"


def test_catalog_has_swatches_for_both_modes():
    from app.memufiles import appearance as ap
    for lang in ("ru", "en"):
        cat = ap.catalog(lang, {"ui_design": "grid"})
        assert [p["id"] for p in cat["palettes"]] == list(ap.PALETTE_IDS)
        hexes = re.compile(r"#[0-9a-f]{6}")
        for p in cat["palettes"]:
            for mode in ("dark", "light"):
                assert all(hexes.fullmatch(p[mode][k] or "") for k in ("bg", "panel", "text", "accent")), (p["id"], mode, p[mode])
        assert len(cat["accents"]) == 12 and all(hexes.fullmatch(a["dark"]) and hexes.fullmatch(a["light"]) for a in cat["accents"])
        assert [d["id"] for d in cat["designs"]] == list(ap.DESIGN_IDS) and all(d["label"] and d["note"] for d in cat["designs"])
        assert [f["id"] for f in cat["folders"]] == list(ap.FOLDER_IDS)
        assert cat["memuai_design"] == "grid"
    assert ap.catalog("en")["designs"][3]["label"] == "Sheet" and ap.catalog("ru")["designs"][3]["label"] == "Лист"


def test_static_designs_and_picker_assets():
    from app.memufiles import appearance as ap
    css = (STATIC / "designs.css").read_text(encoding="utf-8")
    for d in ap.DESIGN_IDS:
        if d not in ("auto", "classic"):
            assert f':root[data-design="{d}"]' in css, d
        assert f'.lk-dz[data-d="{d}"]' in css or d in ("auto", "classic"), d
    for f in ("blue", "accent", "mono"):
        assert f'[data-folders="{f}"]' in css, f
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "designs.css" in html and "js/look.js" in html
    assert html.index("feat41.css") < html.index("designs.css")
    look = (STATIC / "js" / "look.js").read_text(encoding="utf-8")
    emoji = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")
    assert not emoji.search(look) and not emoji.search(css)
    ru = set(re.findall(r'"(lk\.[\w.]+)":', look))
    assert {"lk.menu", "lk.mode", "lk.palette", "lk.accent", "lk.design", "lk.folders", "lk.accent_custom"} <= ru
    # each key is there for both languages
    for key in ru:
        assert look.count(f'"{key}":') == 2, key
    spec = (ROOT / "MemuFiles.spec").read_text(encoding="utf-8")
    assert "'app.memufiles.appearance'" in spec and "'app.memunote.appearance'" in spec
    assert "'app.memufiles.appearance'" in (ROOT / "MemuAI.spec").read_text(encoding="utf-8")


def test_page_boots_in_the_chosen_look(tmp_path):
    from app.memufiles import server as srv, settings
    _prefs(tmp_path, theme="dark", accent_color="terracotta", ui_design="notebook")
    app = srv.FilesApp()
    httpd, _ = srv.start(app)
    try:
        html = call(app, "/index.html").decode("utf-8")
        assert '<html lang="ru" data-theme="dark" data-design="paper" data-folders="classic">' in html
        assert '<style id="mf-theme">' in html and "fallback-tokens" not in html
        settings.save({"theme": "light", "palette": "sepia", "design": "brutal", "folder_color": "accent"})
        html = call(app, "/index.html").decode("utf-8")
        assert 'data-theme="light" data-design="brutal" data-folders="accent"' in html
        cat = call(app, "/api/appearance")
        assert cat["ok"] and cat["theme"]["palette"] == "sepia" and cat["theme"]["design"] == "brutal"
        assert len(cat["palettes"]) == 19 and cat["memuai_design"] == "notebook"
        # MemuAI writes through the schema too: a bad colour is refused as a whole
        res = call(app, "/api/settings", {"accent": "custom", "accent_custom": "#12ab9f"})
        assert res["settings"]["accent"] == "custom" and res["settings"]["accent_custom"] == "#12ab9f"
    finally:
        httpd.shutdown()


def test_ui_picker_menu_and_live_apply(tmp_path, monkeypatch):
    pw = pytest.importorskip("playwright.sync_api")
    from app.memufiles import fsops, server as srv, settings
    monkeypatch.setattr(fsops, "open_default", lambda p: None)
    _prefs(tmp_path, theme="light", accent_color="terracotta", ui_design="classic")
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "Папка").mkdir()
    (tmp_path / "home" / "файл.txt").write_text("x", encoding="utf-8")
    settings.save({"palette": "nord", "design": "glass", "folder_color": "blue"})
    app = srv.FilesApp()
    httpd, _ = srv.start(app)
    shots = os.environ.get("MEMU_SHOTS_DIR")
    try:
        with pw.sync_playwright() as p:
            browser = p.chromium.launch(executable_path=_chromium(), args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1280, "height": 820}, bypass_csp=True)
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.goto(f"http://127.0.0.1:{app.port}/index.html#t={app.token}")
            page.wait_for_selector(".tabbar .tab")
            page.evaluate("p => MemuFiles.pane().navigate(p)", str(tmp_path / "home"))
            page.wait_for_function("MemuFiles.pane().shown.length >= 2")
            from app.memunote import appearance as note
            nord = note.palette_tokens("light", "nord")["--bg"]
            st = page.evaluate("() => ({d: document.documentElement.dataset.design, f: document.documentElement.dataset.folders, bg: getComputedStyle(document.documentElement).getPropertyValue('--bg').trim(), folder: getComputedStyle(document.documentElement).getPropertyValue('--folder').trim()})")
            assert st["d"] == "glass" and st["f"] == "blue" and st["bg"] == nord and st["folder"] == "#4b91e4", st

            page.evaluate("() => import('/js/dialogs.js').then((m) => m.settingsDialog(MemuFiles, 'appearance'))")
            page.wait_for_selector(".lk .lk-card[data-look=palette]")
            assert page.locator(".lk .lk-card[data-look=palette]").count() == 19
            assert page.locator(".lk .lk-card[data-look=design]").count() == 9
            assert page.locator(".lk-card[data-look=palette][data-v=nord][aria-checked=true]").count() == 1
            # the plain rows of the group stay below the picker, without the keys it already shows
            assert page.locator(".st41-row[data-key=palette], .st41-row[data-key=accent], .st41-row[data-key=design]").count() == 0
            page.click(".lk-card[data-look=design][data-v=zen]")
            page.wait_for_function("() => document.documentElement.dataset.design === 'zen'")
            page.click(".lk-chip[data-look=folder_color][data-v=mono]")
            page.click(".lk-card[data-look=palette][data-v=sepia]")
            page.wait_for_function("(bg) => getComputedStyle(document.documentElement).getPropertyValue('--bg').trim() === bg", arg=note.palette_tokens("light", "sepia")["--bg"])
            page.wait_for_function("() => document.documentElement.dataset.folders === 'mono'")
            page.evaluate("() => { const i = document.querySelector('[data-look-color]'); i.value = '#3355ff'; i.dispatchEvent(new Event('input', {bubbles: true})); i.dispatchEvent(new Event('change', {bubbles: true})); }")
            page.wait_for_function("() => MemuFiles.settings.accent === 'custom' && MemuFiles.settings.accent_custom === '#3355ff'")
            page.wait_for_selector(".lk-hex")
            if shots:
                page.screenshot(path=os.path.join(shots, "mf-look-settings.png"))
            page.keyboard.press("Escape")
            page.wait_for_function("() => !document.querySelector('.modal .lk')")
            page.wait_for_timeout(500)
            saved = settings.load()
            assert (saved["design"], saved["folder_color"], saved["palette"], saved["accent"], saved["accent_custom"]) == ("zen", "mono", "sepia", "custom", "#3355ff")

            # the «Look» submenu switches without Settings
            page.evaluate("() => { const b = [...document.querySelectorAll('.cmdbar button')].find((x) => /Ещё/.test(x.title || x.getAttribute('aria-label') || '')); MemuFiles.moreMenu(b); }")
            page.hover(".menu .mi:has-text('Оформление')")
            page.hover(".menu .mi:has-text('Дизайн')")
            page.click(".menu .mi:has-text('Брутализм')")
            page.wait_for_function("() => document.documentElement.dataset.design === 'brutal'")
            assert settings.load()["design"] == "brutal"
            # MemuAI changes its look while MemuFiles follows it: the window repaints on its own
            settings.save({"palette": "auto", "accent": "auto", "design": "auto"})
            _prefs(tmp_path, theme="light", accent_color="terracotta", ui_design="studio")
            page.evaluate("() => MemuFiles.reloadSettings && MemuFiles.reloadSettings()")
            page.wait_for_function("() => document.documentElement.dataset.design === 'studio'", timeout=8000)
            assert not [e for e in errors if "favicon" not in e], errors
            browser.close()
    finally:
        httpd.shutdown()
