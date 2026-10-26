"""Real catalogue UI, isolated Chromium and deterministic offline API responses."""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import unittest
from tests import team_ui_test
from app import module_catalog as catalog

class CatalogUI(unittest.TestCase):
    setUpClass=classmethod(team_ui_test.TeamUI.setUpClass.__func__)
    tearDownClass=classmethod(team_ui_test.TeamUI.tearDownClass.__func__)
    setUp=team_ui_test.TeamUI.setUp
    tearDown=team_ui_test.TeamUI.tearDown

    def mount(self,modified=False):
        root=Path(__file__).resolve().parents[1]
        if not self.page.evaluate("!!window.MemuModuleCatalog"):
            self.page.add_script_tag(path=str(root/"app/pgstatic/module-catalog.js"))
            self.page.add_style_tag(path=str(root/"app/pgstatic/module-catalog.css"))
        seed=catalog._seed()
        entry=catalog.normalize_index(seed["index"],catalog.DEFAULT_SOURCE)["modules"][0]
        raw=seed['packages'][entry['package']['path']]
        payload=json.loads(raw)
        payload['files']['main.js'] += '\nwindow.catalogPreviewWasExecuted = true;\n/* </code><img src=x onerror="window.catalogPreviewWasExecuted=true"> */'
        raw=json.dumps(payload,ensure_ascii=False)
        entry['package'].update(sha256=hashlib.sha256(raw.encode()).hexdigest(),bytes=len(raw.encode()))
        files={'manifest.json':json.dumps(payload['manifest'],ensure_ascii=False,indent=2),**{name:json.dumps(value,ensure_ascii=False,indent=2) if isinstance(value,dict) else value for name,value in payload['files'].items()}}
        preview={'id':entry['id'],'version':entry['version'],'sha256':entry['package']['sha256'],'files':files,'package_text':raw,'package_name':entry['id']+'.memu-module.json'}
        entry.update(installed=modified,installed_version="0.9" if modified else "",enabled=False,compatible=True,status="update" if modified else "available",local_modified=modified,can_rollback=modified,managed=modified)
        self.page.evaluate("""data=>{
          window.mcRequests=[];
          const entry=data.entry,old=fetch;window.mcFixtureEntry=entry;
          window.fetch=async(url,options={})=>{
            const path=String(url),body=options.body?JSON.parse(options.body):{};
            const answer=value=>new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}});
            if(!path.startsWith('/api/pg/module-catalog'))return old(url,options);
            mcRequests.push({path,body});
            if(path.endsWith('/install')||path.endsWith('/rollback')){entry.status='installed';entry.installed=true;entry.installed_version=entry.version;return answer({disabled:true});}
            if(path.includes('/authoring'))return answer({modules:[entry],contributing_url:'https://github.com/MemuAI/MemuAI/blob/main/module-catalog/CONTRIBUTING.md'});
            if(path.endsWith('/prepare'))return answer({package_text:'{}',entry,package_path:'module-catalog/packages/daily-plan.memu-module.json',pull_request_url:'https://github.com/MemuAI/MemuAI/compare'});
            if(path.endsWith('/preview'))return answer(data.preview);
            return answer({source:data.source,default_source:data.source,official:true,mode:'bundled',checked:1,error:'offline fixture',modules:[entry]});
          };
          currentSettingsTab='modules';showSettings(false);
        }""",{"entry":entry,"source":catalog.DEFAULT_SOURCE,"preview":preview})
        self.page.wait_for_selector("#pane-modules .ms-hub")
        self.page.evaluate("MemuModuleCatalog.mount(document.querySelector('#pane-modules'),{back:()=>MemuModules.mountSettings('list')})")
        self.page.wait_for_selector(".mc-card")

    def test_details_search_offline_install_and_narrow_layout(self):
        self.mount()
        self.assertIn("копия",self.page.locator("#mc-banner").inner_text())
        self.page.locator("#mc-search").fill("NO MATCH")
        self.assertEqual(self.page.locator(".mc-card").count(),0)
        self.page.locator("#mc-search").fill("план")
        self.page.locator(".mc-card").click()
        self.assertIn("Свои данные",self.page.locator(".mc-permissions").inner_text())
        self.page.screenshot(path=str(self.output/"module-catalog-dark.png"))
        self.page.locator("#mc-install").click()
        self.page.wait_for_function("document.querySelector('#mc-notice').textContent.includes('Готово')")
        self.assertEqual(self.page.evaluate("mcRequests.find(v=>v.path.endsWith('/install')).body.profile_id"),"default")
        self.page.set_viewport_size({"width":390,"height":844})
        self.assertTrue(self.page.locator("#panel").evaluate("n=>n.scrollWidth<=n.clientWidth+2"))
        self.page.screenshot(path=str(self.output/"module-catalog-narrow.png"))

    def test_local_edits_need_explicit_consent_and_preparation_downloads(self):
        self.mount(True)
        self.page.locator(".mc-card").click()
        self.page.locator("#mc-install").click()
        self.assertEqual(self.page.evaluate("mcRequests.filter(v=>v.path.endsWith('/install')).length"),0)
        self.page.locator("#mc-replace").check()
        self.page.locator("#mc-install").click()
        self.page.wait_for_function("mcRequests.some(v=>v.path.endsWith('/install'))")
        self.assertTrue(self.page.evaluate("mcRequests.find(v=>v.path.endsWith('/install')).body.replace_local"))
        self.page.locator("#mc-contribute").click()
        self.page.locator('[name="author"]').fill("Test author")
        self.page.locator('[name="license"]').fill("MIT")
        self.page.locator('#mc-prepare-form button[type="submit"]').click()
        self.page.wait_for_selector("#mc-package")
        with self.page.expect_download() as event:
            self.page.locator("#mc-package").click()
        self.assertEqual(event.value.suggested_filename,"daily-plan.memu-module.json")

    def test_source_view_has_all_files_without_execution_install_or_repeated_downloads(self):
        self.mount()
        self.page.locator('#mc-code-tab').click()
        self.page.wait_for_selector('[data-mc-file="main.js"]')
        self.assertIn('catalogPreviewWasExecuted',self.page.locator('.mc-code-text').inner_text())
        self.assertFalse(self.page.evaluate('!!window.catalogPreviewWasExecuted'))
        self.assertEqual(self.page.locator('#mc-code-panel img,#mc-code-panel iframe,#mc-code-panel script').count(),0)
        self.page.locator('[data-mc-file="manifest.json"]').click()
        self.assertIn('"permissions"',self.page.locator('.mc-code-text').inner_text())
        self.page.evaluate("()=>{window.sourceCopied='';copyText=async text=>{window.sourceCopied=text;return true;};}")
        self.page.locator('#mc-copy-file').click()
        self.page.wait_for_function("sourceCopied.includes('permissions')")
        with self.page.expect_download() as event:self.page.locator('#mc-source-package').click()
        self.assertEqual(event.value.suggested_filename,'daily-plan.memu-module.json')
        self.page.locator('#mc-description-tab').click();self.page.locator('#mc-code-tab').click()
        self.page.wait_for_selector('[data-mc-file="main.js"]')
        self.assertEqual(self.page.evaluate("mcRequests.filter(v=>v.path.endsWith('/preview')).length"),1)
        self.assertEqual(self.page.evaluate("mcRequests.filter(v=>v.path.endsWith('/install')).length"),0)
        self.page.screenshot(path=str(self.output/'round60-catalog-source-dark.png'))
        self.page.set_viewport_size({'width':390,'height':844})
        self.assertTrue(self.page.locator('#panel').evaluate('n=>n.scrollWidth<=n.clientWidth+2'))
        self.page.locator('#mc-code-panel').scroll_into_view_if_needed()
        self.page.screenshot(path=str(self.output/'round60-catalog-source-narrow.png'))

    def test_search_clears_hidden_detail_and_keyboard_tabs_remain_reachable(self):
        self.mount()
        self.assertTrue(self.page.locator('#mc-detail').is_visible())
        self.page.locator('#mc-search').fill('not found anywhere')
        self.assertTrue(self.page.locator('#mc-detail').is_hidden())
        self.page.locator('#mc-search').fill('план')
        self.page.locator('.mc-card').click()
        self.page.locator('#mc-description-tab').focus();self.page.keyboard.press('ArrowRight')
        self.page.wait_for_selector('[data-mc-file="main.js"]')
        self.assertEqual(self.page.locator('#mc-code-tab').get_attribute('aria-selected'),'true')
        self.page.keyboard.press('ArrowLeft')
        self.assertEqual(self.page.locator('#mc-description-tab').get_attribute('aria-selected'),'true')

    def test_source_loading_failure_can_retry_and_copy_failure_is_honest(self):
        self.mount()
        self.page.evaluate("""()=>{const old=fetch;let failed=false;window.fetch=(url,options)=>{
          if(String(url).endsWith('/preview')&&!failed){failed=true;return Promise.resolve(new Response(JSON.stringify({error:'Temporary download failure'}),{status:502}));}
          return old(url,options);
        };}""")
        self.page.locator('#mc-code-tab').click();self.page.wait_for_selector('#mc-source-retry')
        self.page.locator('#mc-source-retry').click();self.page.wait_for_selector('[data-mc-file="main.js"]')
        self.page.evaluate('()=>{copyText=async()=>false;}')
        self.page.locator('#mc-copy-file').click()
        self.page.wait_for_function("document.querySelector('#mc-code-status').textContent.includes('Не удалось')")

    def test_refresh_resets_a_category_removed_from_the_catalogue(self):
        self.mount()
        self.page.locator('#mc-category').select_option('productivity')
        self.page.evaluate("mcFixtureEntry.category='utilities'")
        self.page.locator('#mc-refresh').click()
        self.page.wait_for_function("document.querySelector('#mc-category').value==='' && document.querySelector('#mc-category option[value=utilities]')")
        self.assertEqual(self.page.locator('.mc-card').count(),1)

    def test_restored_panel_refresh_does_not_accept_an_older_inflight_response(self):
        self.mount()
        self.page.evaluate("""async()=>{
          const previous=fetch, snapshot=await (await previous('/api/pg/module-catalog?profile_id=default')).json();let count=0;
          window.fetch=(url,options)=>{
            if(String(url).startsWith('/api/pg/module-catalog?')){
              const data=structuredClone(snapshot);data.modules[0].title=++count===1?'Older response':'Latest response';
              const answer=()=>new Response(JSON.stringify(data));
              if(count===1)return new Promise(resolve=>window.releaseOldCatalogue=()=>{resolve(answer());window.oldCatalogueReleased=true;});
              return Promise.resolve(answer());
            }
            return previous(url,options);
          };
          document.dispatchEvent(new CustomEvent('memu:panel-restored',{detail:{panel:document.querySelector('#panel')}}));
        }""")
        self.page.wait_for_function('!!window.releaseOldCatalogue')
        self.page.evaluate("document.dispatchEvent(new CustomEvent('memu:panel-restored',{detail:{panel:document.querySelector('#panel')}}))")
        self.page.wait_for_function("document.querySelector('#mc-detail h3').textContent==='Latest response'")
        self.page.evaluate('releaseOldCatalogue()')
        self.page.wait_for_timeout(60)
        self.assertEqual(self.page.locator('#mc-detail h3').inner_text(),'Latest response')

if __name__=="__main__":unittest.main()
