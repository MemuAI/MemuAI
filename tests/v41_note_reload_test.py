import os
import unittest

from tests import presentations_v4_test as studio
from tests.round32_slides_test import _chromium
from tests.round40_note_test import make_xlsx


@unittest.skipUnless(_chromium(), 'Chromium unavailable')
class ExternalReloadTests(unittest.TestCase):
    setUp = studio.StudioBrowserTests.setUp
    tearDown = studio.StudioBrowserTests.tearDown

    def change_file(self, path, data):
        prior = path.stat().st_mtime
        path.write_bytes(data)
        os.utime(path, (prior + 10, prior + 10))
        self.pg.wait_for_function('''path => MN.tabs.find(t => t.path === path)?.mtime > window.beforeMtime''', arg=str(path), timeout=15000)

    def test_external_sheet_change_reloads_rows_and_replaces_destroyed_grid(self):
        from app.memunote.formats.xlsx_edit import patch_xlsx
        path = self.dir / 'external.xlsx'
        data = make_xlsx()
        path.write_bytes(data)
        self.fixture.open(path, wait='.sg')
        self.pg.evaluate("window.sheetTab = MN.tabs.find(t => t.kind === 'sheet'); window.oldComp = sheetTab.comp; window.beforeMtime = sheetTab.mtime")
        self.assertEqual(self.pg.evaluate('sheetTab.comp.raw(0, 0, sheetTab.comp.sheets[0])'), 'Товар')
        updated = patch_xlsx(data, {'sheets': [{'name': 'Продажи', 'cells': {'A1': {'v': 'Changed on disk'}}}]})
        self.change_file(path, updated)
        self.pg.wait_for_function("() => sheetTab.comp?.raw(0, 0, sheetTab.comp.sheets[0]) === 'Changed on disk'", timeout=5000)
        self.assertTrue(self.pg.evaluate('sheetTab.comp !== oldComp'))
        self.assertFalse(self.pg.evaluate('sheetTab.dirty'))
        self.assertEqual(self.errors, [])

    def test_external_pdf_change_builds_a_live_new_document(self):
        from pypdf import PdfWriter
        import io
        def pdf(pages):
            writer = PdfWriter()
            for _ in range(pages):
                writer.add_blank_page(width=300, height=400)
            output = io.BytesIO()
            writer.write(output)
            return output.getvalue()
        path = self.dir / 'external.pdf'
        path.write_bytes(pdf(1))
        self.fixture.open(path, wait='.pdf-page-box')
        self.pg.evaluate("window.pdfTab = MN.tabs.find(t => t.kind === 'pdf'); window.oldComp = pdfTab.comp; window.beforeMtime = pdfTab.mtime")
        self.change_file(path, pdf(2))
        self.pg.wait_for_function("() => document.querySelectorAll('.pdf-page-box').length === 2", timeout=5000)
        self.assertTrue(self.pg.evaluate('pdfTab.comp !== oldComp'))
        self.assertFalse(self.pg.evaluate('pdfTab.dirty'))
        self.assertEqual(self.errors, [])

    def test_failed_external_reload_keeps_live_grid_and_retries_without_losing_mtime(self):
        from app.memunote.formats.xlsx_edit import patch_xlsx
        path = self.dir / 'temporarily-locked.xlsx'
        data = make_xlsx()
        path.write_bytes(data)
        self.fixture.open(path, wait='.sg')
        self.pg.evaluate("window.sheetTab = MN.tabs.find(t => t.kind === 'sheet'); window.oldComp = sheetTab.comp; window.beforeMtime = sheetTab.mtime")
        calls = []
        def temporary_failure(route):
            calls.append(route.request.url)
            if len(calls) == 1:
                route.fulfill(status=503, json={'error': 'File temporarily locked'})
            else:
                route.continue_()
        self.pg.route('**/api/open', temporary_failure)
        prior = path.stat().st_mtime
        path.write_bytes(patch_xlsx(data, {'sheets': [{'name': 'Продажи', 'cells': {'A1': {'v': 'After retry'}}}]}))
        os.utime(path, (prior + 10, prior + 10))
        self.pg.wait_for_selector('.banner [data-b="reload"]', timeout=10000)
        self.assertTrue(self.pg.evaluate('sheetTab.comp === oldComp && oldComp.el.isConnected'))
        self.assertTrue(self.pg.evaluate('sheetTab.mtime === beforeMtime'))
        self.pg.wait_for_function("() => sheetTab.comp?.raw(0, 0, sheetTab.comp.sheets[0]) === 'After retry'", timeout=10000)
        self.assertFalse(self.pg.evaluate('sheetTab.dirty'))
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
