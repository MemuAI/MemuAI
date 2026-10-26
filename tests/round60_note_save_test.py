import json
import unittest

from tests import presentations_v4_test as studio
from tests.round32_slides_test import _chromium

@unittest.skipUnless(_chromium(), 'Chromium unavailable')
class SaveRegressions(unittest.TestCase):
    setUp = studio.StudioBrowserTests.setUp
    tearDown = studio.StudioBrowserTests.tearDown
    new_starter = studio.StudioBrowserTests.new_starter
    view = studio.StudioBrowserTests.view
    _wait_js = studio.StudioBrowserTests._wait_js

    def delay(self, endpoint):
        self.pg.evaluate("""endpoint=>{
          const original=fetch;
          window.fetch=(url,options={})=>{
            if(String(url).startsWith('/api/'+endpoint)){
              window.savedRequest=options.body;
              return new Promise(resolve=>window.finishSave=()=>resolve(new Response(JSON.stringify({ok:true,mtime:Date.now(),size:100,sheets:[{name:'Sheet1',rows:[['before']],formulas:{}}]}),{headers:{'Content-Type':'application/json'}})));
            }
            return original(url,options);
          };
        }""", endpoint)

    def test_slide_undo_branch_does_not_reuse_a_discarded_saved_position(self):
        self.new_starter()
        path = str(self.dir / 'saved.pptx')
        self.view("v.change(()=>{v.slide.notes='saved'})")
        self.pg.evaluate("async path=>{const v=MN.tabs.find(t=>t.kind==='slides').comp;await v.save(path,{force:true});}", path)
        self.assertFalse(self.view('v.dirty'))
        self.view("(()=>{v.undo();v.change(()=>{v.slide.notes='new unsaved branch'});return true})()")
        self.assertTrue(self.view('v.dirty'))
        self.assertEqual(self.errors, [])

    def test_slide_edits_during_save_keep_both_view_and_tab_dirty(self):
        self.new_starter()
        self.pg.evaluate("path=>{const tab=MN.tabs.find(t=>t.kind==='slides');tab.path=path;tab.mtime=0;}", str(self.dir / 'race.pptx'))
        self.delay('save_binary')
        self.pg.wait_for_selector('.modal-back.in', state='detached')
        self.pg.locator('.sl-stage').click()
        self.pg.keyboard.press('Control+s')
        self.pg.wait_for_function("()=>typeof finishSave==='function'")
        self.view("v.change(()=>{v.slide.notes='edited while saving'})")
        self.pg.evaluate('finishSave()')
        self.pg.wait_for_function("()=>!MN.tabs.find(t=>t.kind==='slides').saving")
        self.assertTrue(self.view('v.dirty'))
        self.assertTrue(self.pg.evaluate("MN.tabs.find(t=>t.kind==='slides').dirty"))
        self.assertEqual(self.errors, [])

    def test_sheet_save_response_preserves_newer_edits_and_new_sheets(self):
        self.delay('sheet_save')
        self.pg.evaluate("""async()=>{
          const {SheetGrid}=await import('/js/sheetgrid.js');
          const host=document.createElement('div');host.style.cssText='height:500px;width:800px';document.body.append(host);
          window.grid=new SheetGrid(host,{sheetEditable:true,sheets:[{name:'Sheet1',rows:[[1]],formulas:{}}]});
          grid.apply([{sheet:grid.sheet,r:0,c:0,value:'before'}]);
          window.sheetSave=grid.save('/unused.xlsx');
        }""")
        self.pg.wait_for_function("()=>typeof finishSave==='function'")
        self.pg.evaluate("grid.apply([{sheet:grid.sheet,r:0,c:0,value:'after'}]);grid.addSheet('New while saving');finishSave()")
        self.pg.evaluate('sheetSave')
        self.assertEqual(self.pg.evaluate('grid.raw(0,0,grid.sheets[0])'), 'after')
        self.assertTrue(self.pg.evaluate('grid.isDirty'))
        self.assertEqual(self.pg.evaluate('grid.changes().new_sheets'), [{'name':'New while saving'}])
        self.assertEqual(self.errors, [])

    def test_rich_edits_during_save_remain_dirty(self):
        path = str(self.dir / 'document.docx')
        self.fixture.call('POST', '/api/save_rich', {'path':path,'html':'<p>initial</p>','format':'docx','force':True})
        self.fixture.open(path, wait='.doc-page')
        self.pg.evaluate("()=>{MN.settings.rich_save_warning=false;window.richTab=MN.tabs.find(t=>t.kind==='docx');richTab.doc.page.innerHTML='<p>before</p>';richTab.doc.opts.onChange();}")
        self.delay('save_rich')
        self.pg.locator('.doc-page').click()
        self.pg.keyboard.press('Control+s')
        self.pg.wait_for_function("()=>typeof finishSave==='function'")
        self.pg.evaluate("richTab.doc.page.innerHTML='<p>after</p>';richTab.doc.opts.onChange();finishSave()")
        self.pg.wait_for_function('()=>!richTab.saving')
        self.assertTrue(self.pg.evaluate('richTab.dirty'))
        self.assertIn('after', self.pg.evaluate('richTab.doc.html()'))
        self.assertEqual(self.errors, [])

    def test_pdf_save_keeps_later_annotations_and_the_saved_undo_marker(self):
        path = str(self.dir / 'document.pdf')
        self.pg.evaluate("""async path=>{
          const {PDFDocument}=await import('/vendor/pdflib/pdflib.js');
          const document=await PDFDocument.create();document.addPage([300,400]);
          const bytes=await document.save();
          const {api}=await import('/js/core.js');
          await api('save_binary?'+new URLSearchParams({path,force:'1'}),new Blob([bytes]),{raw:true});
        }""", path)
        self.fixture.open(path, wait='.pdf-page-box')
        self.pg.evaluate("""async()=>{
          window.pdfTab=MN.tabs.find(t=>t.kind==='pdf');await pdfTab.comp.setEditing(true);
          pdfTab.comp.pageOp(0,'add');
        }""")
        self.delay('save_binary')
        self.pg.locator('.pdf-scroll').click()
        self.pg.keyboard.press('Control+s')
        self.pg.wait_for_function("()=>typeof finishSave==='function'")
        self.pg.evaluate("pdfTab.comp.pageOp(1,'add');finishSave()")
        self.pg.wait_for_function('()=>!pdfTab.saving')
        self.assertEqual(self.pg.evaluate('pdfTab.comp.model.pages.length'), 3)
        self.assertTrue(self.pg.evaluate('pdfTab.dirty'))
        self.pg.evaluate('pdfTab.comp.undo()')
        self.assertFalse(self.pg.evaluate('pdfTab.comp.dirty'))
        self.assertEqual(self.errors, [])

if __name__ == '__main__':
    unittest.main()
