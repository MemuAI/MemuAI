"""Settings latency, module creation and profile/help regression checks."""
from __future__ import annotations

import json
import unittest

from tests import team_ui_test

class SettingsUI(unittest.TestCase):
    setUpClass = classmethod(team_ui_test.TeamUI.setUpClass.__func__)
    tearDownClass = classmethod(team_ui_test.TeamUI.tearDownClass.__func__)
    setUp = team_ui_test.TeamUI.setUp
    tearDown = team_ui_test.TeamUI.tearDown

    def test_settings_paints_before_slow_catalogs_and_reuses_them(self):
        result=self.page.evaluate("""async () => {
          const old=fetch;window.slowCatalogCalls=0;
          window.fetch=async (url,options)=>{
            if(['/api/pg/prompts','/api/voice/catalog','/api/voice/realtime/catalog'].includes(String(url))) {
              slowCatalogCalls++;await new Promise(resolve=>setTimeout(resolve,650));
            }
            return old(url,options);
          };
          currentSettingsTab='general';
          const started=performance.now();window.settingsReady=showSettings(false);
          const immediate=!!document.querySelector('#panel .sx-shell');
          await new Promise(requestAnimationFrame);
          return {immediate,paint:performance.now()-started,nodes:document.querySelectorAll('#panel *').length,
            inactivePickers:document.querySelectorAll('.settings-tab-pane:not(.active) .pretty-select,.settings-tab-pane:not(.active) .model-picker-select').length};
        }""")
        self.assertTrue(result["immediate"])
        self.assertLess(result["paint"],550)
        self.assertEqual(result["inactivePickers"],0)
        self.page.evaluate("settingsReady")
        self.page.evaluate("closePanel();showSettings(false)")
        self.assertEqual(self.page.evaluate("slowCatalogCalls"),3)
        (self.output / "settings-performance.json").write_text(json.dumps(result,indent=2),encoding="utf-8")

    def test_module_wizard_has_steps_validation_stable_draft_and_narrow_layout(self):
        from app.modules_host import templates, PERMISSIONS
        self.page.evaluate("""data=>{
          const old=fetch;
          window.fetch=(url,opts)=>{
            if(String(url)==='/api/pg/modules/templates')return Promise.resolve(new Response(JSON.stringify(data)));
            if(String(url)==='/api/pg/modules')return Promise.resolve(new Response(JSON.stringify({modules:[],removed:[]})));
            return old(url,opts);
          };
          currentSettingsTab='modules';showSettings(false);
        }""",{"templates":templates(),"permissions":list(PERMISSIONS),"slots":["sidebar","chat-list","topbar","composer"],"icons":["text","chat"]})
        self.page.locator('[data-modules-tab="create"]').click()
        self.page.wait_for_selector("#mw-next")
        self.assertEqual(self.page.locator("[data-create-panel]:visible").count(),1)
        self.page.locator("#mw-next").click()
        self.page.locator("#mw-title").fill("Мои заметки")
        self.page.locator("#mw-description").fill("Личные записи и быстрые идеи")
        self.assertEqual(self.page.locator("#mw-id").input_value(),"moi-zametki")
        self.page.locator("#mw-next").click()
        self.page.locator('[data-ms-size="1120,720"]').click()
        self.assertEqual(self.page.locator('#mw-width').input_value(),'1120')
        self.assertEqual(self.page.locator('#mw-height').input_value(),'720')
        self.page.locator("#mw-width").fill("99999")
        self.page.locator("#mw-next").click()
        self.assertTrue(self.page.locator("#mw-width-error").is_visible())
        self.page.locator("#mw-width").fill("760")
        self.page.locator("#mw-next").click()
        self.assertTrue(self.page.locator("#mw-create").is_visible())
        self.page.locator('[data-create-step="1"]').click()
        self.assertEqual(self.page.locator("#mw-title").input_value(),"Мои заметки")
        self.assertEqual(self.page.locator("#mw-description").input_value(),"Личные записи и быстрые идеи")
        self.page.screenshot(path=str(self.output / "module-wizard-dark.png"))
        self.page.set_viewport_size({"width":390,"height":844})
        self.assertLessEqual(self.page.locator(".module-wizard-layout").evaluate("n=>n.getBoundingClientRect().right"),390)
        self.assertTrue(self.page.locator("#panel").evaluate("n=>n.scrollWidth <= n.clientWidth+2"))
        self.page.screenshot(path=str(self.output / "module-wizard-narrow.png"))

    def test_profile_check_is_left_aligned_and_help_dot_stays_read(self):
        self.page.locator("#profile-card").click()
        self.page.wait_for_selector(".profile-menu-row.active .profile-selected")
        positions=self.page.evaluate("""()=>{
          const row=document.querySelector('.profile-menu-row.active').getBoundingClientRect();
          const check=document.querySelector('.profile-menu-row.active .profile-selected').getBoundingClientRect();
          const avatar=document.querySelector('.profile-menu-row.active .profile-avatar').getBoundingClientRect();
          return {gap:check.left-row.left,beforeAvatar:check.right<=avatar.left};
        }""")
        self.assertLess(positions["gap"],24)
        self.assertTrue(positions["beforeAvatar"])
        self.page.evaluate("localStorage.setItem('memu_tips_v1',JSON.stringify({v:1,welcome:'done',seen:{},off:false,dot:true}))")
        self.page.reload()
        self.page.wait_for_function("document.body.classList.contains('boot-ready')")
        self.assertEqual(self.page.locator("#btn-help.has-dot").count(),0)

        self.page.reload()
        self.page.wait_for_function("document.body.classList.contains('boot-ready')")
        self.assertEqual(self.page.locator("#btn-help.has-dot").count(),0)
    def test_installed_module_library_alignment_and_collapsed_access(self):
        import tempfile
        from pathlib import Path
        from app.modules_host import list_modules
        with tempfile.TemporaryDirectory() as folder:
            rows=list_modules(Path(folder))
        import base64
        for row in rows:
            icon=Path(__file__).resolve().parents[1]/'app/module_builtins'/row['id']/row.get('icon_src','icon.svg')
            if icon.is_file():row['icon_url']='data:image/svg+xml;base64,'+base64.b64encode(icon.read_bytes()).decode()
        self.page.evaluate("""async rows=>{
          const old=fetch;window.fetch=(url,opts)=>String(url)==='/api/pg/modules'?Promise.resolve(new Response(JSON.stringify({modules:rows,removed:[]}))):old(url,opts);
          await MemuModules.refresh();currentSettingsTab='modules';await showSettings(false);await MemuModules.mountSettings('list');
        }""",rows)
        self.page.wait_for_selector('.ms-module')
        self.assertFalse(self.page.locator('.ms-global').get_attribute('open'))
        self.page.locator('.ms-module').first.click()
        actions=self.page.locator('.ms-actions button')
        self.assertGreaterEqual(actions.first.bounding_box()['height'],38)
        positions=actions.evaluate_all('nodes=>nodes.map(n=>({height:n.getBoundingClientRect().height,center:n.getBoundingClientRect().y+n.getBoundingClientRect().height/2}))')
        self.assertLess(max(p['center'] for p in positions)-min(p['center'] for p in positions),2)
        self.page.screenshot(path=str(self.output/'module-library-feedback.png'))
        self.page.set_viewport_size({'width':440,'height':840})
        self.assertTrue(self.page.locator('#panel').evaluate('n=>n.scrollWidth<=n.clientWidth+2'))

if __name__=="__main__":unittest.main()
