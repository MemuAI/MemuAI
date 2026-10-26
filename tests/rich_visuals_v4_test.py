"""Real Markdown renderer and offline native interactions; no provider calls."""
import json
import unittest
from tests import team_ui_test

class RichVisuals(unittest.TestCase):
    setUpClass = classmethod(team_ui_test.TeamUI.setUpClass.__func__)
    tearDownClass = classmethod(team_ui_test.TeamUI.tearDownClass.__func__)
    setUp = team_ui_test.TeamUI.setUp
    tearDown = team_ui_test.TeamUI.tearDown

    def show(self, block, before="Before", after="After"):
        raw=before+"\n\n```memuai-visual\n"+json.dumps(block,ensure_ascii=False)+"\n```\n\n"+after
        self.page.evaluate("s=>{document.querySelector('#messages').innerHTML=md(s);MemuVisuals.hydrate(document.querySelector('#messages'));}",raw)
        return raw

    def test_reference_flow_ranking_comparison_and_copy(self):
        block={"type":"text","title":"Workflow","blocks":[
            {"type":"flow","steps":[{"icon":"folder","title":"PDF arrived"},{"icon":"brain","title":"Read PDF"},{"type":"branch","title":"Over ten pages?","branches":[{"label":"Yes","blocks":[{"type":"text","text":"Detailed summary"}]},{"label":"No","blocks":[{"type":"text","text":"Short summary"}]}]},{"icon":"note","title":"Save in MemuNote"}]},
            {"type":"ranking","title":"Ideas","max":10,"items":[{"name":"Zebra","value":10,"detail":"Useful"},{"name":"Alpha","value":8}]},
            {"type":"comparison","items":[{"name":"ChatGPT","text":"A complete g","badge":"Works","tone":"success"},{"name":"MemuAI","text":"g g g g g","badge":"Works too","tone":"success"}]}]}
        raw=self.show(block)
        self.assertEqual(self.page.locator('.rv-branches > div').count(),2)
        self.assertEqual(self.page.locator('.rv-comparison-items > article').count(),2)
        self.page.locator('[data-rv-sort="name"]').click()
        self.assertEqual(self.page.locator('.rv-ranking-row strong').first.inner_text(),'Alpha')
        self.page.evaluate("s=>{document.querySelector('#messages').innerHTML=md(s);MemuVisuals.hydrate(document.querySelector('#messages'));}",raw)
        self.assertEqual(self.page.locator('.rv-ranking-row strong').first.inner_text(),'Alpha')
        copied=self.page.evaluate('s=>MemuVisuals.stripForText(s)',raw)
        self.assertIn('Zebra: 10/10',copied)
        self.assertIn('Detailed summary',copied)
        self.assertNotIn('```',copied)
        self.page.screenshot(path=str(self.output/'rich-visuals-reference.png'),full_page=True)

    def test_inline_entity_panel_image_preferences_and_code_literals(self):
        raw='Try [[entity:libre|LibreChat]]. `[[entity:libre|literal]]`\n\n```memuai-entity\n'+json.dumps({"id":"libre","name":"LibreChat","summary":"An example description","inline_only":True,"image":"https://example.com/image.png","facts":[{"label":"Stars","value":"46.2k"}],"sources":[{"title":"Website","url":"https://example.com"}]})+'\n```'
        self.page.evaluate("s=>{document.querySelector('#messages').innerHTML=md(s)}",raw)
        self.assertEqual(self.page.locator('button[data-rv-entity]').count(),1)
        self.page.locator('button[data-rv-entity]').click()
        self.assertEqual(self.page.locator('.rv-sheet h2').inner_text(),'LibreChat')
        self.assertIn('/api/pg/media?url=',self.page.locator('.rv-sheet img').get_attribute('src'))
        self.page.keyboard.press('Escape')
        self.assertEqual(self.page.locator('.rv-sheet').count(),0)
        self.assertIn('Try LibreChat.',self.page.evaluate('s=>MemuVisuals.stripForText(s)',raw))
        self.assertIn('`[[entity:libre|literal]]`',self.page.evaluate('s=>MemuVisuals.stripForText(s)',raw))
        self.page.evaluate("async()=>{await savePreferences({visualizations_images_enabled:false});mdCache.clear()}")
        self.show({"type":"entity","name":"Illustration","image":"https://example.com/img.png","summary":"Text"})
        self.assertEqual(self.page.locator('#messages img').count(),0)

    def test_tabs_calculator_validation_and_untrusted_content(self):
        self.show({"type":"text","blocks":[{"type":"tabs","items":[{"label":"One","blocks":[{"type":"text","text":"First panel"}]},{"label":"Two","blocks":[{"type":"text","text":"Second panel"}]}]},{"type":"calculator","inputs":[{"id":"n","label":"Count","min":0,"max":20,"value":3}],"formula":"n*2","unit":"USD"},{"type":"text","text":"<img src=x onerror=alert(1)>"}]})
        self.page.locator('[data-rv-tab="0"]').focus();self.page.keyboard.press('ArrowRight')
        self.assertTrue(self.page.locator('[data-rv-tab-panel="1"]').is_visible())
        self.page.locator('[data-rv-input]').fill('7')
        self.assertIn('14',self.page.locator('.rv-calculator output').inner_text())
        self.page.locator('[data-rv-input]').fill('30')
        self.assertIn('—',self.page.locator('.rv-calculator output').inner_text())
        self.assertEqual(self.page.locator('#messages img').count(),0)
        self.assertFalse(self.page.evaluate("MemuRichVisuals.validate({type:'calculator',formula:'window.alert(1)'}).ok"))
        self.assertFalse(self.page.evaluate("MemuRichVisuals.validate({type:'ranking',items:[{name:'A',value:'NaN'}]}).ok"))

    def test_custom_sandbox_clean_copy_and_disable(self):
        block={"type":"custom","summary":"Two options, A and B.","html":"<div>Options A and B</div><script>try{window.localRead=typeof parent.S}catch(e){window.localRead='blocked'}</script>"}
        raw=self.show(block)
        frame=self.page.locator('iframe.memuai-visual-frame')
        self.assertEqual(frame.get_attribute('sandbox'),'allow-scripts')
        self.assertIn("default-src 'none'",frame.get_attribute('srcdoc'))
        self.assertIn('Two options, A and B.',self.page.evaluate('s=>MemuVisuals.stripForText(s)',raw))
        self.page.evaluate("async()=>{await savePreferences({visualizations_custom_enabled:false});mdCache.clear()}")
        self.show(block)
        self.assertEqual(self.page.locator('iframe.memuai-visual-frame').count(),0)
        self.assertIn('Two options, A and B.',self.page.locator('#messages').inner_text())
        self.page.evaluate("async()=>{await savePreferences({visualizations_enabled:false});mdCache.clear()}")
        self.show({"type":"ranking","max":10,"items":[{"name":"A","value":9}]})
        self.assertEqual(self.page.locator('.rv-ranking').count(),0)
        self.assertIn('A: 9/10',self.page.locator('#messages').inner_text())

    def test_specialized_badges_and_telegram_descender_space(self):
        result=self.page.evaluate("""()=>{
          const kinds=['image','video','music','audio','tts','stt','embedding','rerank','decisions'];
          return kinds.map(kind=>({kind,html:MemuModelPicker.badgesHtml({id:kind,kind,caps:{}},{noKind:true})}));
        }""")
        for row in result:
            self.assertIn('mmp-kind',row['html'],row['kind'])
            self.assertIn('<svg',row['html'],row['kind'])
        self.page.evaluate("currentSettingsTab='telegram';showSettings(false)")
        label=self.page.locator('.settings-tab-btn[data-set-tab="remote"] .sx-nav-text')
        self.assertTrue(label.is_visible())
        sizes=label.evaluate("e=>({font:parseFloat(getComputedStyle(e).fontSize),height:e.getBoundingClientRect().height,padding:parseFloat(getComputedStyle(e).paddingBottom)})")
        self.assertGreaterEqual(sizes['height'],sizes['font']*1.5)
        self.assertGreaterEqual(sizes['padding'],2)

    def test_malformed_members_and_local_media_are_safe(self):
        for kind in ['flow','branch','comparison','cards','timeline','list','tabs','accordion','ranking','chart','calculator']:
            result=self.page.evaluate("""kind=>{
              const value={type:kind,items:[null],steps:[null],branches:[null],inputs:[null],data:[null]};
              return {html:MemuRichVisuals.render(JSON.stringify(value)),text:MemuRichVisuals.plain(value),valid:MemuRichVisuals.validate(value)};
            }""",kind)
            self.assertIsInstance(result['html'],str)
        self.show({'type':'entity','name':'Local image','image':'/api/pg/media/file/'+'a'*64+'.png?profile_id=default'})
        self.assertEqual(self.page.locator('#messages img').count(),1)

    def test_identical_instances_preserve_independent_state(self):
        block={'type':'ranking','items':[{'name':'Zebra','value':10},{'name':'Alpha','value':8}]}
        raw='```memuai-visual\n'+json.dumps(block)+'\n```\n\n```memuai-visual\n'+json.dumps(block)+'\n```'
        render="s=>{document.querySelector('#messages').innerHTML=md(s);MemuVisuals.hydrate(document.querySelector('#messages'));}"
        self.page.evaluate(render,raw)
        self.page.locator('[data-rv-sort="name"]').first.click()
        self.page.evaluate(render,raw)
        self.assertEqual(self.page.locator('.rv-ranking-row strong').all_inner_texts(),['Alpha','Zebra','Zebra','Alpha'])

if __name__=='__main__':unittest.main()
