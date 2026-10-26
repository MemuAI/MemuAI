"""Persistence and palette contracts; stdlib-only, no desktop/network required."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import pgdata, themes

ROOT = Path(__file__).resolve().parents[1]
PALETTES = tuple(themes.ACCENTS)

def luminance(value: str) -> float:
    rgb = [int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    rgb = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
    return sum(a * b for a, b in zip(rgb, (.2126, .7152, .0722)))

def ratio(a: str, b: str) -> float:
    low, high = sorted((luminance(a), luminance(b)))
    return (high + .05) / (low + .05)

class AppearancePreferencesTests(unittest.TestCase):
    def test_default_and_invalid_values_are_normalized(self):
        for value in [{}, {'accent_color': 'invalid', 'chat_width': 'huge'}, {'accent_color': None},
                      {'accent_color': ['blue']}, {'surface_dark': 'paper', 'surface_light': 'nord'},
                      {'surface_dark': 'custom:../../x', 'custom_themes_allowed': 1}]:
            p = pgdata._normalize_preferences(value)
            self.assertEqual(p['accent_color'], 'terracotta')
            self.assertEqual(p['chat_width'], 'comfortable')
            self.assertEqual(p['surface_dark'], '')
            self.assertEqual(p['surface_light'], '')

    def test_variants_are_validated_per_mode_and_custom_refs_are_kept(self):
        p = pgdata._normalize_preferences({'surface_dark': 'nord', 'surface_light': 'latte'})
        self.assertEqual((p['surface_dark'], p['surface_light']), ('nord', 'latte'))
        p = pgdata._normalize_preferences({'surface_dark': 'custom:my-theme'})
        self.assertEqual(p['surface_dark'], 'custom:my-theme')

    def test_legacy_surface_style_moves_to_its_mode(self):
        p = pgdata._normalize_preferences({'theme': 'dark', 'surface_style': 'midnight'})
        self.assertEqual(p['surface_dark'], 'midnight')
        p = pgdata._normalize_preferences({'theme': 'light', 'surface_style': 'paper'})
        self.assertEqual(p['surface_light'], 'paper')
        self.assertNotIn('surface_style', p)

    def test_every_accent_survives_save_and_load_without_losing_existing_preferences(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(pgdata, 'MEMU_DIR', Path(temp)):
            pgdata.save_preferences({'profile_name': 'Existing profile', 'font_scale': 110, 'theme': 'light'})
            for accent in PALETTES:
                saved = pgdata.save_preferences({'accent_color': accent, 'chat_width': 'wide',
                                                 'surface_dark': 'mocha'})
                loaded = pgdata.load_preferences()
                self.assertEqual(loaded, saved)
                self.assertEqual(loaded['accent_color'], accent)
                self.assertEqual(loaded['chat_width'], 'wide')
                self.assertEqual(loaded['surface_dark'], 'mocha')
                self.assertEqual(loaded['profile_name'], 'Existing profile')
                self.assertEqual(loaded['font_scale'], 110)
                self.assertEqual(loaded['theme'], 'light')

    def test_assets_are_loaded_and_explicitly_integrated(self):
        html = (ROOT / 'app/pgstatic/index.html').read_text(encoding='utf-8')
        js = (ROOT / 'app/pgstatic/app.js').read_text(encoding='utf-8')
        self.assertLess(html.index('/appearance.js?'), html.index('/app.js?'))
        self.assertIn('/appearance.css?', html)
        self.assertLess(html.index('/themes.css'), html.index('/appearance.css?'))
        for call in ('MemuAppearance.apply(pref)', 'MemuAppearance.render(PG_LANG, S.theme, pref)',
                     'MemuAppearance.bind(', 'MemuAppearance.syncTheme(theme)'):
            self.assertIn(call, js)

    def test_every_variant_meets_aa_contrast_with_every_accent(self):
        """Text, captions, all twelve accents and syntax colours stay readable."""
        failures = []
        for mode in ('dark', 'light'):
            for row in themes.VARIANTS[mode]:
                tokens = themes.variant_tokens(mode, row['id'])
                name = f"{mode}/{row['id'] or 'default'}"
                surfaces = [tokens[key] for key in ('--bg', '--bg-deep', '--panel', '--panel-2')]
                checks = [('code-text', tokens['--code-text'], tokens['--code-bg']),
                          ('danger', tokens['--danger'], tokens['--panel']),
                          ('ok', tokens['--ok'], tokens['--panel'])]
                for surface in surfaces:
                    checks += [('text', tokens['--text'], surface), ('muted', tokens['--muted'], surface)]
                    checks += [(f'accent {key}', accent[mode], surface) for key, accent in themes.ACCENTS.items()]
                checks += [(f'on-accent {key}', tokens['--on-accent'], accent[mode])
                           for key, accent in themes.ACCENTS.items()]
                checks += [(key, tokens[key], tokens['--code-bg']) for key in tokens if key.startswith('--tok-')]
                failures += [f'{name}: {label} {fg} on {bg} = {ratio(fg, bg):.2f}'
                             for label, fg, bg in checks if ratio(fg, bg) < 4.5]
        self.assertEqual(failures, [], '\n'.join(failures))

    def test_generated_stylesheet_covers_every_variant_and_accent(self):
        css = themes.theme_css()
        for mode, rows in themes.VARIANTS.items():
            self.assertIn(f':where([data-theme="{mode}"]) {{', css)
            for row in rows[1:]:
                self.assertIn(f':where([data-theme="{mode}"][data-surface="{row["id"]}"])', css)

        for line in css.splitlines():
            if line.endswith('{'):
                self.assertTrue(line.startswith(':where('), line)
        for accent in PALETTES:
            self.assertIn(accent, css)
        self.assertIn('--tok-kw', css)

if __name__ == '__main__':
    unittest.main()
