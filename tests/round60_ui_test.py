import unittest
from tests import round58_feedback_test as fixture

class UI(unittest.TestCase):
    setUpClass = classmethod(fixture.UI.setUpClass.__func__)
    tearDownClass = classmethod(fixture.UI.tearDownClass.__func__)
    setUp = fixture.UI.setUp
    tearDown = fixture.UI.tearDown
    module_fixture = fixture.UI.module_fixture

    def test_preferences_are_serial_and_newer_changes_survive_an_old_response(self):
        self.page.evaluate("""()=>{
          window.prefRequests=[];window.serverPrefs={...S.preferences};const old=fetch;
          window.fetch=async(url,options={})=>{
            if(String(url)==='/api/pg/preferences' && options.method==='POST'){
              const body=JSON.parse(options.body);prefRequests.push(body);
              if(prefRequests.length===1)await new Promise(resolve=>window.releasePref=resolve);
              serverPrefs={...serverPrefs,...body};return new Response(JSON.stringify(serverPrefs));
            }
            return old(url,options);
          };
          window.prefOne=savePreferences({profile_name:'First'});
        }""")
        self.page.wait_for_function('prefRequests.length===1')
        self.page.evaluate("()=>{window.prefTwo=savePreferences({profile_name:'Second'});}")
        self.page.wait_for_timeout(180)
        self.assertEqual(self.page.evaluate('prefRequests.length'),1)
        self.page.evaluate('releasePref()')
        self.page.evaluate('Promise.all([prefOne,prefTwo])')
        self.assertEqual(self.page.evaluate('S.preferences.profile_name'),'Second')
        self.assertEqual(self.page.evaluate('serverPrefs.profile_name'),'Second')

    def test_sidebar_failed_save_never_shows_saved(self):
        self.page.evaluate("""()=>{const old=fetch;window.fetch=(url,options={})=>String(url)==='/api/pg/preferences' && options.method==='POST'?Promise.resolve(new Response(JSON.stringify({error:'Offline'}),{status:503})):old(url,options);}""")
        self.page.locator('#tm-nav-customize').click()
        self.page.locator('#sb-visible-work').uncheck()
        self.page.wait_for_selector('#sb-save-status.error')
        self.assertNotEqual(self.page.locator('#sb-save-status').inner_text(),'Сохранено')

    def test_hidden_customization_has_permanent_appearance_entry_and_restores_after_reload(self):
        self.page.evaluate("currentSettingsTab='appearance';showSettings(false)")
        self.page.locator('label.switch:has(#st-sidebar-customize)').click()
        self.page.wait_for_function('qaPrefs.sidebar_customize_visible===false')
        self.assertTrue(self.page.locator('#tm-nav-customize').is_hidden())
        self.page.locator('#st-customize-sidebar').click()
        self.page.wait_for_selector('.sb-settings')
        self.page.reload();self.page.wait_for_function("document.body.classList.contains('boot-ready')")
        self.assertTrue(self.page.locator('#tm-nav-customize').is_hidden())
        self.page.evaluate("currentSettingsTab='appearance';showSettings(false)")
        self.page.locator('label.switch:has(#st-sidebar-customize)').click()
        self.page.wait_for_function('qaPrefs.sidebar_customize_visible===true')
        self.assertTrue(self.page.locator('#tm-nav-customize').is_visible())

    def test_sidebar_shows_actual_module_icon(self):
        self.module_fixture()
        self.page.locator('#tm-nav-customize').click()
        self.page.wait_for_selector('label[for="sb-module-alpha"] [data-mico="alpha"]')

    def test_stale_module_read_cannot_undo_a_configuration_write(self):
        self.module_fixture()
        self.page.evaluate("""()=>{
          const old=fetch;window.fetch=(url,options={})=>{
            if(String(url)==='/api/pg/modules' && (!options.method || options.method==='GET')){
              const snapshot=JSON.stringify({modules:moduleRows,removed:[]});
              return new Promise(resolve=>window.releaseModules=()=>resolve(new Response(snapshot)));
            }
            return old(url,options);
          };
          window.moduleRead=MemuModules.refresh();
        }""")
        self.page.evaluate("MemuModules.configure('alpha',{hidden:true})")
        self.page.evaluate('releaseModules();moduleRead')
        self.assertTrue(self.page.evaluate('MemuModules.catalog[0].launcher.hidden'))
        self.assertEqual(self.page.locator('#module-slot-sidebar [data-module-open="alpha"]').count(),0)

    def test_late_enable_does_not_navigate_away_from_module_creation(self):
        self.module_fixture()
        self.page.evaluate("""()=>{
          const old=fetch;window.fetch=async(url,options={})=>{
            if(String(url)==='/api/pg/modules/configure')await new Promise(resolve=>window.releaseModuleWrite=resolve);
            return old(url,options);
          };
          currentSettingsTab='modules';showSettings(false);
        }""")
        self.page.locator('[data-module-enabled="alpha"]').uncheck()
        self.page.locator('[data-modules-tab="create"]').click()
        self.page.wait_for_selector('.ms-create')
        self.page.evaluate('releaseModuleWrite()')
        self.page.wait_for_function('moduleRows[0].enabled===false')
        self.page.wait_for_timeout(100)
        self.assertTrue(self.page.locator('.ms-create').is_visible())

if __name__ == '__main__':
    unittest.main()
