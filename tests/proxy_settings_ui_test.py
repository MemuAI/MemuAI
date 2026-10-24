"""Round 23: the redesigned Proxy Settings page (app/static/settings*.js/css, #tab-settings).

Static contracts only (fast, no browser): every control still saves a real config key, the
"restore default" values match app/config.py, RU/EN strings are complete, no id of Round 22 is
lost, scripts load in the right order and the Proxy-only build rule holds. The browser checks of
navigation and search live in tests/proxy_settings_browser.py (needs a running Proxy)."""
from __future__ import annotations

import dataclasses
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def settings_tab(html: str) -> str:
    start = html.index('<section class="tab" id="tab-settings">')
    return html[start:html.index("<!-- ============ ЛОГИ", start)]


class ProxySettingsPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = read("index.html")
        cls.tab = settings_tab(cls.html)
        cls.js = read("settings.js")
        cls.i18n = read("settings-i18n.js")
        cls.css = read("settings.css")
        cls.app = read("app.js")

    def test_every_control_saves_a_real_setting(self) -> None:
        from app.config import Settings
        fields = {f.name for f in dataclasses.fields(Settings)}
        keys = re.findall(r'data-setting="([a-z_]+)"', self.tab)
        self.assertGreaterEqual(len(keys), 22)
        self.assertEqual(len(keys), len(set(keys)), "one control per key")
        self.assertLessEqual(set(keys), fields)
        # Everything the Round 22 page saved is still on the page.
        for key in ("open_ui_on_start", "close_to_tray", "balancing", "cookie_check_interval", "use_prefixes",
                    "strict_models", "host", "port", "cors_enabled", "default_proxy", "verify_ssl",
                    "first_byte_timeout", "request_timeout", "stream_idle_timeout", "max_retry_passes",
                    "retry_delay_ms", "sse_keepalive", "sse_keepalive_interval", "log_requests", "log_persist",
                    "log_payloads", "metrics_enabled", "search_results"):
            self.assertIn(f'data-setting="{key}"', self.tab, key)

    def test_restore_default_values_match_the_backend(self) -> None:
        from app.config import Settings
        block = self.js[self.js.index("const DEFAULTS = {") + len("const DEFAULTS = "):]
        block = block[:block.index("};") + 1]
        as_json = re.sub(r",\s*}", "}", re.sub(r"([{,]\s*)([a-z_]+):", r'\1"\2":', block))
        defaults = json.loads(as_json)
        real = Settings()
        for key, value in defaults.items():
            self.assertEqual(getattr(real, key), value, key)
        on_page = set(re.findall(r'data-setting="([a-z_]+)"', self.tab))
        self.assertEqual(set(defaults), on_page, "every scalar control can be restored")

    def test_strings_are_complete_in_both_languages(self) -> None:
        ru = self.i18n[self.i18n.index("Object.assign(I18N.ru"):self.i18n.index("Object.assign(I18N.en")]
        en = self.i18n[self.i18n.index("Object.assign(I18N.en"):]
        key = re.compile(r'"(sg\.[a-z0-9_.]+)":')
        ru_keys, en_keys = set(key.findall(ru)), set(key.findall(en))
        self.assertEqual(ru_keys, en_keys)
        self.assertEqual(len(key.findall(ru)), len(ru_keys), "no key twice in RU")
        self.assertEqual(len(key.findall(en)), len(en_keys), "no key twice in EN")
        used = set(re.findall(r'data-i18n(?:-html|-ph|-aria)?="(sg\.[a-z0-9_.]+)"', self.tab))
        used |= set(re.findall(r't\("(sg\.[a-z0-9_.]+)"\)', self.js + self.app))
        self.assertLessEqual(used, ru_keys, used - ru_keys)
        # Markup in a description goes through data-i18n-html, never data-i18n (textContent would show tags).
        for name in re.findall(r'data-i18n="(sg\.[a-z0-9_.]+)"', self.tab):
            self.assertNotIn("<code>", re.search(r'"' + re.escape(name) + r'": "([^"]*)"', ru).group(1), name)

    def test_round22_ids_are_kept(self) -> None:
        backup = ROOT.parent / "memu-r22-backup" / "app" / "static" / "index.html"
        if not backup.exists():
            self.skipTest("Round 22 backup tree is not available")
        old = settings_tab(backup.read_text(encoding="utf-8"))
        for element_id in re.findall(r'\bid="([^"]+)"', old):
            self.assertIn(f'id="{element_id}"', self.html, element_id)

    def test_sections_navigation_and_search_are_wired(self) -> None:
        sections = re.findall(r'<section class="sg-sec[^"]*" id="sg-sec-([a-z]+)"', self.tab)
        nav = re.findall(r'data-sg-nav="([a-z]+)"', self.tab)
        self.assertEqual(sections, nav, "the rail lists the sections in page order")
        listed = json.loads(re.search(r"const SECTIONS = (\[[^\]]+\])", self.js).group(1))
        self.assertEqual(sorted(listed), sorted(sections))
        for needle in ('id="sg-search"', 'id="sg-found"', 'id="sg-empty"', 'id="settings-status"',
                       'role="status" aria-live="polite"'):
            self.assertIn(needle, self.tab)
        # Every segmented control drives a hidden select that exists.
        for target in re.findall(r'class="sg-seg" role="radiogroup" data-for="([^"]+)"', self.tab):
            self.assertRegex(self.tab, rf'<select id="{re.escape(target)}"[^>]* hidden')
        # The danger zone reuses the existing confirmations, not new API calls.
        for selector in ("#proxy-restart", "#metrics-reset", "#logs-clear"):
            self.assertIn(f'data-sg-click="{selector}"', self.tab)
            self.assertIn(f'id="{selector[1:]}"', self.html)
        self.assertNotIn("api(", self.js, "settings.js makes no API calls of its own")

    def test_scripts_load_in_order(self) -> None:
        head = self.html[:self.html.index("</head>")]
        self.assertIn('<script src="/theme.js"></script>', head)
        self.assertIn('href="/settings.css"', head)
        # A cache version (?v=…) may follow the name (Round 34: app.js?v=3.5-r34).
        order = [re.search(rf'src="/{re.escape(name)}(?:\?v=[^"]*)?"', self.html).start() for name in ("i18n.js", "settings-i18n.js", "app.js", "settings.js")]
        self.assertEqual(order, sorted(order))
        self.assertIn("MemuSettings.sync()", self.app)
        self.assertIn("MemuSettings.show()", self.app)
        self.assertIn("MemuSettings.relabel()", self.app)

    def test_saving_sends_the_same_values_as_before(self) -> None:
        # Switches send booleans; provider switches send the provider's own strings ("true"/"on").
        self.assertIn('if (el.type === "checkbox") return el.dataset.on !== undefined ? (el.checked ? el.dataset.on : el.dataset.off) : el.checked;', self.app)
        self.assertIn("autoSaveSetting({ [el.dataset.setting]: settingValue(el) })", self.app)
        self.assertIn("settings: (p.settings || []).map", self.app, "provider settings are translated too")

    def test_proxy_only_and_performance_rules(self) -> None:
        for name in ("settings.js", "settings-i18n.js", "settings.css", "theme.js"):
            text = read(name)
            self.assertIsNone(re.search(r"\bMemuAI\b(?!\s+Proxy)", text), name)
        self.assertNotIn(":has(", self.css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.css)
        self.assertNotIn("navigator.clipboard", self.js)
        theme = read("theme.js")
        self.assertIn('localStorage.getItem("oh_theme") || "dark"', theme, "the default look is unchanged")


if __name__ == "__main__":
    unittest.main()
