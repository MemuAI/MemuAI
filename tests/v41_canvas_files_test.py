import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from app import canvas_files as files


class CanvasFilesRegressions(unittest.TestCase):
    def test_zip_export_sanitizes_titles_without_rejecting_valid_file_paths(self):
        from app import canvas
        content = files.pack([('index.html', '<h1>Kept content</h1>')])
        titles = ('CON.txt', 'LPT².report', 'a' * 79 + '.tail', 'CON.' + 'a' * 90, '⟦Project⟧')
        for title in titles:
            with self.subTest(title=title):
                data, _filename, media = canvas.export_bytes({'type': 'html', 'title': title}, {'content': content})
                self.assertEqual(media, 'application/zip')
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    path = archive.namelist()[0]
                    self.assertEqual(files.clean_path(path), path)
                    self.assertEqual(archive.read(path), b'<h1>Kept content</h1>')

    def test_hidden_files_are_addressable_and_traversal_never_aliases_them(self):
        bundle = [('.env', 'example'), ('.github/workflows/build.yml', 'steps: []')]
        self.assertEqual(files.find_file(bundle, '.env'), 0)
        self.assertEqual(files.find_file(bundle, './.env'), 0)
        self.assertEqual(files.find_file(bundle, '.github/workflows/build.yml'), 1)
        self.assertEqual(files.find_file(bundle, '../.env'), -1)

    def test_export_paths_are_relative_and_extractable_on_windows(self):
        self.assertEqual(files.clean_path('./.config/config.json'), '.config/config.json')
        for path in ('/absolute.txt', '\\absolute.txt', '//server/share.txt', 'CON', 'aux.txt',
                     'folder/NUL.log', 'Lpt1.txt', 'COM9', 'folder.', 'folder /file.txt'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                files.clean_path(path)

    def test_missing_bundle_lines_do_not_map_to_a_phantom_file_line(self):
        bundle = files.pack([('a.txt', 'first\nlast')])
        self.assertEqual(files.file_of_line(bundle, -1), ('', 0))
        self.assertEqual(files.file_of_line(bundle, 3), ('', 0))
        self.assertEqual(files.file_of_line(bundle, 1000), ('', 0))
        self.assertEqual(files.file_of_line(bundle, 2), ('a.txt', 2))

    def test_root_index_wins_with_case_insensitive_html_extensions(self):
        self.assertEqual(files.entry_of([('ABOUT.html', ''), ('index.HTML', '')]), 'index.HTML')
        self.assertEqual(files.entry_of([('ABOUT.html', ''), ('INDEX.htm', '')]), 'INDEX.htm')

    def test_inline_asset_end_tags_are_escaped_case_insensitively(self):
        assembled = files.assemble([('index.html', '<body><h1>Heading</h1></body>'),
                                   ('app.js', 'window.result="</ScRiPt>"; window.finished=true;'),
                                   ('style.css', '/* </STYLE> */ h1 { color: red; }')])
        self.assertNotIn('</ScRiPt>', assembled['html'])
        self.assertNotIn('</STYLE>', assembled['html'])
        self.assertIn('window.finished=true;', assembled['html'])

    def test_zip_export_refuses_path_traversal_in_legacy_or_imported_bundles(self):
        for path in ('../outside.txt', '/absolute.txt', 'CON.txt'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                files.to_zip([(path, 'text')])
        with self.assertRaises(ValueError):
            files.to_zip([('a.txt', '')], '../outside')
        with zipfile.ZipFile(io.BytesIO(files.to_zip([('.env', 'example')], 'project'))) as archive:
            self.assertEqual(archive.namelist(), ['project/.env'])

    def test_mixed_case_inline_assets_execute_in_isolated_headless_browser(self):
        from tests.round32_slides_test import _chromium
        if not _chromium():
            self.skipTest('Chromium unavailable')
        from playwright.sync_api import sync_playwright
        assembled = files.assemble([('index.html', '<body><h1>Heading</h1></body>'),
                                   ('app.js', 'window.result="</ScRiPt>"; window.finished=true;'),
                                   ('style.css', '/* </STYLE> */ h1 { color: red; }')])
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=_chromium(), headless=True)
            try:
                page = browser.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.set_content(assembled['html'])
                self.assertTrue(page.evaluate('window.finished'))
                self.assertEqual(page.evaluate('window.result'), '</ScRiPt>')
                self.assertEqual(page.locator('h1').evaluate('el => getComputedStyle(el).color'), 'rgb(255, 0, 0)')
                self.assertEqual(errors, [])
            finally:
                browser.close()

    def test_legacy_invalid_zip_export_returns_400_and_preserves_saved_bundle(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app import canvas
        with tempfile.TemporaryDirectory() as temporary, patch.object(canvas, '_root', lambda: Path(temporary)):
            app = FastAPI()
            canvas.register(app)
            with TestClient(app, raise_server_exceptions=False) as client:
                for name in ('../outside.txt', '/absolute.txt', 'CON.txt'):
                    with self.subTest(name=name):
                        original = files.mark(name) + '\nSaved user content'
                        artifact = canvas.create('legacy', 'code', 'Legacy project', original)
                        stored = Path(temporary, 'legacy.json').read_bytes()
                        response = client.get(f"/api/pg/canvas/legacy/{artifact['id']}/export")
                        self.assertEqual(response.status_code, 400, response.text)
                        self.assertFalse(response.json()['ok'])
                        self.assertIn('path', response.json()['error'])
                        self.assertEqual(Path(temporary, 'legacy.json').read_bytes(), stored)
                        self.assertEqual(canvas.content_of('legacy', artifact['id'])[1]['content'], original)
                artifact = canvas.create('valid', 'code', 'Project', files.pack([('.env', 'example')]))
                response = client.get(f"/api/pg/canvas/valid/{artifact['id']}/export")
                self.assertEqual(response.status_code, 200)
                with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                    self.assertEqual(archive.read('Project/.env'), b'example')


if __name__ == '__main__':
    unittest.main()
