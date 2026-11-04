"""Extended interface settings for MemuNote and MemuFiles: tab styles, movable/hideable
panels, custom CSS (sanitised, window-only), column toggles, and the wiring that makes
the new settings show up in the apps and in the MemuAI settings page. No network."""

import json
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MN_STATIC = ROOT / "app" / "memunote" / "static"
MF_STATIC = ROOT / "app" / "memufiles" / "static"

MN_NEW = {
    "tab_style": "classic",
    "show_tab_bar": True,
    "tab_meta": False,
    "sidebar_side": "left",
    "sidebar_width": 264,
    "md_preview_side": "right",
}
MF_NEW = {
    "tab_style": "classic",
    "show_tab_bar": True,
    "tab_meta": False,
    "sidebar_side": "left",
    "show_sidebar": True,
    "preview_side": "right",
    "columns_size": True,
    "columns_date": True,
}
EVIL_CSS = '@import url(http://evil/x.css); @charset "utf-8"; .tab { background: url(https://evil/x.png); color: red; content: "</style><script>"; x: expression(alert(1)); }'


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMUAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MEMUFILES_TRASH_DIR", str(tmp_path / "trash"))
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    yield


def test_schema_has_new_interface_keys():
    from app.memunote import settings_schema as mn
    from app.memufiles import settings_schema as mf

    for key, default in MN_NEW.items():
        assert key in mn.BY_KEY, key
        assert mn.BY_KEY[key]["group"] == "appearance", key
        assert mn.DEFAULTS[key] == default, key
    for key, default in MF_NEW.items():
        assert key in mf.BY_KEY, key
        assert mf.BY_KEY[key]["group"] == "appearance", key
        assert mf.DEFAULTS[key] == default, key
    # every new choice is labelled in both languages
    for sch in (mn, mf):
        for key in ("tab_style", "sidebar_side"):
            item = sch.BY_KEY[key]
            assert [c["value"] for c in item["choices"]] == (
                ["classic", "compact", "icons", "preview", "vertical"] if key == "tab_style" else ["left", "right"]
            )
            assert all(c.get("ru") and c.get("en") for c in item["choices"]), key
    # custom CSS is a hidden key: it never reaches the MemuAI settings page
    for sch in (mf, mn):
        assert "custom_css" in sch.BY_KEY
        assert sch.BY_KEY["custom_css"]["hidden"] is True
        assert "custom_css" not in {i["key"] for i in sch.public()["items"]}
    # validation: good values pass, bad ones are refused with a reason
    ok, _ = mn.validate({"tab_style": "vertical", "sidebar_width": 300, "md_preview_side": "left"})
    assert ok == {"tab_style": "vertical", "sidebar_width": 300, "md_preview_side": "left"}
    _, errs = mn.validate({"tab_style": "nope", "sidebar_width": 9999, "sidebar_side": "up"})
    assert set(errs) == {"tab_style", "sidebar_width", "sidebar_side"}
    ok, _ = mf.validate({"tab_style": "icons", "preview_side": "left", "columns_size": False})
    assert ok == {"tab_style": "icons", "preview_side": "left", "columns_size": False}


def test_custom_css_sanitizer():
    from app.memunote.settings import sanitize_custom_css as f1
    from app.memufiles.settings import sanitize_custom_css as f2

    for f in (f1, f2):
        out = f(EVIL_CSS)
        assert "@import" not in out and "@charset" not in out
        assert "https://evil" not in out and "http://evil" not in out
        assert "</style>" not in out and "<" not in out and ">" not in out
        assert "expression(" not in out and "javascript:" not in out
        assert "color: red" in out  # the harmless rule survives
        assert f('.a{background:url(data:image/png;base64,AAA)}').count("data:image/png") == 1
        assert f("x" * 30000) == "x" * 20000
        assert f(None) == "" and f(42) == ""


def test_memunote_normalize_keeps_new_keys_and_sanitizes_css():
    from app.memunote import settings as store

    clean = store.normalize({"tab_style": "vertical", "sidebar_side": "right", "sidebar_width": 400,
                             "md_preview_side": "left", "custom_css": EVIL_CSS})
    assert clean["tab_style"] == "vertical" and clean["sidebar_side"] == "right"
    assert clean["sidebar_width"] == 400 and clean["md_preview_side"] == "left"
    assert "@import" not in clean["custom_css"] and "color: red" in clean["custom_css"]
    # unknown values fall back to the defaults; out-of-range numbers are clamped
    assert store.normalize({"tab_style": "nope", "sidebar_width": 5})["tab_style"] == "classic"
    assert store.normalize({"tab_style": "nope", "sidebar_width": 5})["sidebar_width"] == 160


def test_memufiles_settings_roundtrip_and_migration(tmp_path):
    from app.memufiles import settings as store

    saved = store.save({"tab_style": "preview", "sidebar_side": "right", "preview_side": "left",
                        "columns_size": False, "custom_css": EVIL_CSS})
    assert saved["tab_style"] == "preview" and saved["sidebar_side"] == "right"
    assert saved["preview_side"] == "left" and saved["columns_size"] is False
    assert "@import" not in saved["custom_css"] and "color: red" in saved["custom_css"]
    assert store.load()["tab_style"] == "preview"  # survives a restart
    # an old settings.json without the new keys loads with the defaults
    path = store._path()
    old = json.loads(path.read_text(encoding="utf-8"))
    for key in list(MF_NEW) + ["custom_css"]:
        old.pop(key, None)
    old["tab_style"] = "nope"  # and a bad stored value is repaired, not kept
    path.write_text(json.dumps(old), encoding="utf-8")
    loaded = store.load()
    assert loaded["tab_style"] == "classic" and loaded["sidebar_side"] == "left"
    assert loaded["columns_size"] is True and loaded["custom_css"] == ""


def _call(port, token_header, token, path, body=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={token_header: token, "Content-Type": "application/json"},
        method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=10) as res:
        raw = res.read()
    return json.loads(raw) if raw[:1] == b"{" else raw


def test_memufiles_server_settings_roundtrip():
    from app.memufiles import server as srv

    app = srv.FilesApp()
    httpd, _ = srv.start(app)
    try:
        res = _call(app.port, "X-MF-Token", app.token, "/api/settings",
                    {"tab_style": "vertical", "show_sidebar": False, "custom_css": EVIL_CSS})
        assert res["settings"]["tab_style"] == "vertical"
        assert res["settings"]["show_sidebar"] is False
        assert "@import" not in res["settings"]["custom_css"]
        res = _call(app.port, "X-MF-Token", app.token, "/api/settings/schema")
        keys = {i["key"] for i in res["items"]}
        assert {"tab_style", "show_tab_bar", "tab_meta", "sidebar_side", "show_sidebar",
                "preview_side", "columns_size", "columns_date"} <= keys
        assert "custom_css" not in keys  # hidden: never offered on the MemuAI page
    finally:
        httpd.shutdown()


def test_memunote_server_settings_roundtrip():
    from app.memunote import server as srv

    app = srv.NoteApp()
    httpd, _ = srv.start(app)
    try:
        res = _call(app.port, "X-MN-Token", app.token, "/api/settings",
                    {"tab_style": "icons", "sidebar_width": 320, "custom_css": EVIL_CSS})
        assert res["settings"]["tab_style"] == "icons"
        assert res["settings"]["sidebar_width"] == 320
        assert "@import" not in res["settings"]["custom_css"]
        assert "color: red" in res["settings"]["custom_css"]
        again = _call(app.port, "X-MN-Token", app.token, "/api/settings")
        assert again["settings"]["tab_style"] == "icons"  # survives a restart
    finally:
        httpd.shutdown()


def test_static_files_wire_the_new_ui():
    mn_js = (MN_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    mf_js = (MF_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    mn_css = (MN_STATIC / "memunote.css").read_text(encoding="utf-8")
    mf_css = (MF_STATIC / "memufiles.css").read_text(encoding="utf-8")
    # the tab strip can move into a side column; custom CSS goes into one <style> element
    for js in (mn_js, mf_js):
        assert "layoutTabs" in js and "applyCustomCss" in js and "tablist-side" in js
        assert 'getElementById("mn-custom-css")' in js or 'getElementById("mf-custom-css")' in js
    assert "tabMeta" in mn_js and "tab_meta" in mf_js
    assert "applyLayout" in mf_js and "columns_size" in (MF_STATIC / "js" / "pane.js").read_text(encoding="utf-8")
    # the CSS editor lives in the in-app settings (appearance group)
    assert "st.css_title" in (MF_STATIC / "js" / "feat41.js").read_text(encoding="utf-8")
    assert "set_custom_css" in mn_js and "data-css-editor" in mn_js
    # the new styles exist in both apps
    for css in (mn_css, mf_css):
        assert '[data-tabstyle="vertical"]' in css and ".tablist-side" in css
        assert '[data-tabstyle="preview"] .tab .tab-sub' in css
        assert '[data-tabstyle="icons"] .tab' in css and ".css-area" in css
    assert "body.sidebar-right .sidebar" in mn_css and "body.md-preview-left .side-host" in mn_css
    assert '.body[data-side="right"] .sidebar' in mf_css
    assert '.body[data-preview-side="left"] .rightpanel' in mf_css
    # the new labels exist in both languages (MemuNote i18n, MemuFiles strings)
    i18n = (MN_STATIC / "js" / "i18n.js").read_text(encoding="utf-8")
    for key in ("set_tab_style", "tab_style_vertical", "set_show_tab_bar", "set_tab_meta",
                "set_sidebar_side", "set_sidebar_width", "set_md_preview_side",
                "set_custom_css", "custom_css_apply", "custom_css_reset"):
        assert key in i18n, key
    feat41 = (MF_STATIC / "js" / "feat41.js").read_text(encoding="utf-8")
    for key in ("st.css_title", "st.css_hint", "st.css_apply", "st.css_reset", "st.css_saved"):
        assert feat41.count(key) >= 2, key  # ru + en
