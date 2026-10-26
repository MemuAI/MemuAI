"""Round58: user's actual profile target, Jev contract, and the module library."""
import unittest
from unittest.mock import patch, AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import model_capabilities as caps, model_catalog, admin_api
from tests import team_ui_test

class ModelContract(unittest.TestCase):
    def test_capability_summary_uses_upstream_identity_for_an_opaque_alias(self):
        from types import SimpleNamespace
        entry=SimpleNamespace(model='typesafe/jev-1.13',prefix='connection',provider='custom')
        with patch.object(caps,'describe',return_value={'summary':{'kind':'chat','text':True},'fields':{}}),patch.object(caps,'_summary_cache',{}):
            self.assertEqual(caps.capabilities_for('connection-my-alias',entry=entry)['kind'],'decisions')

    def test_sidebar_preferences_preserve_legacy_and_ignore_unknown_sections(self):
        from app.pgdata import _normalize_preferences
        value=_normalize_preferences({'team_sidebar_visible':False,'sidebar_sections':{'work':'false','inbox':'true','unknown':False}})
        self.assertFalse(value['team_sidebar_visible'])
        self.assertEqual(value['sidebar_sections'],{'work':False,'inbox':True})
        self.assertEqual(_normalize_preferences({'sidebar_sections':['work']})['sidebar_sections'],{})
    def test_actual_jev_ids_and_stale_chat_metadata(self):
        for model in ['typesafe/jev-1.13','or-typesafe/jev-1.13','~typesafe/jev-latest','jev-latest']:
            with self.subTest(model=model):
                self.assertEqual(caps.kind_for_row({'id':model,'kind':'chat','caps':{'text':True}}),'decisions')
        self.assertEqual(caps.kind_for_row({'id':'new-special-model','kind':'chat','architecture':{'output_modalities':['decisions']}}),'decisions')
        self.assertEqual(caps.extract_provider({'id':'new-special-model','architecture':{'output_modalities':['decisions']}})['kind'],'decisions')
        for model in ['gpt-4o-audio-preview','deepseek-r1','claude-sonnet-4.5']:
            self.assertEqual(caps.kind_for_row({'id':model,'kind':'chat'}),'chat')

    def test_catalog_reclassifies_jev_instead_of_trusting_old_chat_kind(self):
        payload={'models':[{'id':'or-typesafe/jev-1.13','kind':'chat','caps':{}},{'id':'demo-text','caps':{}}]}
        with patch.object(model_catalog,'vision_state',return_value={'state':'unknown','src':'none'}):model_catalog.enrich(payload)
        self.assertEqual([r['id'] for r in model_catalog.pick_rows(payload['models'],kind='text')],['demo-text'])

    def test_server_rejects_jev_before_any_model_transport(self):
        app=FastAPI();app.include_router(admin_api.router)
        with patch.object(admin_api,'_auth',return_value=None),patch.object(admin_api.state,'find_model',return_value=None),patch('app.chat.chat_service.stream_completion') as transport:
            response=TestClient(app).post('/api/admin/playground/chat',json={'model':'or-typesafe/jev-1.13','messages':[{'role':'user','content':'Hello'}]})
        self.assertEqual(response.status_code,400)
        self.assertEqual(response.json()['error_code'],'model_not_chat')
        transport.assert_not_called()

class UI(unittest.TestCase):
    setUpClass=classmethod(team_ui_test.TeamUI.setUpClass.__func__)
    tearDownClass=classmethod(team_ui_test.TeamUI.tearDownClass.__func__)
    setUp=team_ui_test.TeamUI.setUp
    tearDown=team_ui_test.TeamUI.tearDown

    def test_profile_menu_hit_area_across_all_designs_and_both_themes(self):
        import json
        from pathlib import Path
        self.page.evaluate('()=>{window.profileHits=[];openProfileFromMenu=id=>profileHits.push(id);}')
        result=[]
        for design in ['default','focus','soft','glass','aurora','terminal','grid','brutal']:
            style=Path(__file__).resolve().parents[1]/'app/pgstatic/designs'/(design+'.css')
            if style.is_file():self.page.add_style_tag(path=str(style))
            for theme in ['dark','light']:
                self.page.evaluate("([d,t])=>{document.documentElement.dataset.design=d;setTheme(t,false);document.querySelector('#profile-menu-pop').style.display='none';}",[design,theme])
                self.page.locator('#profile-card').click()
                box=self.page.locator('.profile-menu-row.active').bounding_box()
                for fx,fy in [(.02,.5),(.12,.5),(.35,.5),(.6,.5),(.86,.5),(.98,.5),(.5,.1),(.5,.9),(.9,.2),(.9,.8)]:
                    before=self.page.evaluate('profileHits.length')
                    self.page.mouse.move(box['x']+fx*box['width'],box['y']+fy*box['height'])
                    self.page.mouse.down();self.page.wait_for_timeout(80);self.page.mouse.up()
                    hit=self.page.evaluate('profileHits.length')==before+1
                    result.append({'design':design,'theme':theme,'x':fx,'y':fy,'hit':hit})
                    self.assertTrue(hit,(design,theme,fx,fy))
        (self.output/'profile-hit-area-round58.json').write_text(json.dumps(result,indent=2),encoding='utf-8')

    def module_fixture(self):
        self.page.evaluate("""()=>{
          window.moduleRows=[{id:'alpha',title:'Alpha',title_en:'Alpha',enabled:true,permissions:[],launcher:{slot:'sidebar',hidden:false},version:'1'},
            {id:'beta',title:'Beta',title_en:'Beta',enabled:false,permissions:[],launcher:{slot:'topbar',hidden:false},version:'1'}];
          window.moduleWrites=[];const old=fetch;
          window.fetch=async(url,options={})=>{
            const path=String(url).split('?')[0],body=options.body?JSON.parse(options.body):{};
            const answer=value=>new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}});
            if(path==='/api/pg/modules')return answer({modules:moduleRows,removed:[]});
            if(path==='/api/pg/modules/templates')return answer({templates:[],permissions:[],slots:[],icons:[]});
            if(path==='/api/pg/modules/configure'){
              moduleWrites.push(body);const row=moduleRows.find(r=>r.id===body.id);
              for(const key of ['slot','hidden'])if(key in body)row.launcher[key]=body[key];
              if('enabled' in body)row.enabled=body.enabled;
              return answer({module:row});
            }
            return old(url,options);
          };
          return MemuModules.refresh();
        }""")

    def test_sidebar_all_sections_persist_and_remain_recoverable(self):
        self.page.locator('#tm-nav-customize').click()
        self.assertEqual(self.page.locator('[data-sb-section]').count(),10)
        for key in ['work','code','schedule','inbox','skills','modules','pinned','folders','memes']:
            self.page.locator(f'#sb-visible-{key}').uncheck()
        self.page.locator('#tm-nav-team-visible').uncheck()
        self.page.wait_for_function("qaPrefs.sidebar_sections?.memes===false && qaPrefs.team_sidebar_visible===false")
        self.page.reload();self.page.wait_for_function("document.body.classList.contains('boot-ready')")
        for selector in ['#nav-work','#nav-code','#nav-sched','#nav-inbox','#nav-team','#nav-skills','#module-slot-sidebar','#group-folders','#group-memes']:
            self.assertTrue(self.page.locator(selector).is_hidden(),selector)
        self.assertTrue(self.page.locator('#tm-nav-customize').is_visible())
        self.assertTrue(self.page.locator('#nav-chat').is_visible())
        widths=self.page.evaluate("({chat:document.querySelector('#nav-chat').getBoundingClientRect().width,group:document.querySelector('.mode-switch').getBoundingClientRect().width})")
        self.assertGreater(widths['chat'],widths['group']*.9)
        self.page.locator('#tm-nav-customize').click();self.page.locator('#sb-show-all').click()
        self.page.wait_for_function("qaPrefs.sidebar_sections?.memes===true && qaPrefs.team_sidebar_visible===true")
        self.assertTrue(self.page.locator('#nav-work').is_visible())
        self.page.screenshot(path=str(self.output/'sidebar-settings-dark.png'))
        self.page.set_viewport_size({'width':390,'height':844})
        self.assertTrue(self.page.locator('#panel').evaluate('n=>n.scrollWidth<=n.clientWidth+2'))
        self.page.screenshot(path=str(self.output/'sidebar-settings-narrow.png'))

    def test_sidebar_module_shortcuts_share_settings_and_preserve_enabled_state(self):
        self.module_fixture()
        self.page.locator('#tm-nav-customize').click()
        self.page.locator('#sb-module-alpha').uncheck()
        self.page.wait_for_function("moduleRows[0].launcher.hidden===true && !document.querySelector('#module-slot-sidebar [data-module-open=alpha]')")
        self.assertTrue(self.page.evaluate('moduleRows[0].enabled'))
        self.assertEqual(self.page.locator('#module-slot-sidebar [data-module-open="alpha"]').count(),0)
        self.page.locator('#sb-module-beta').check()
        self.page.wait_for_function("moduleRows[1].launcher.slot==='sidebar'")
        self.assertFalse(self.page.evaluate('moduleRows[1].enabled'))
        self.page.locator('#sb-module-settings').click()
        self.page.locator('[data-ms-select="alpha"]').click()
        self.assertTrue(self.page.locator('[data-module-hidden="alpha"]').is_checked())
        self.page.locator('[data-module-hidden="alpha"]').uncheck()
        self.page.wait_for_function('!moduleRows[0].launcher.hidden')
        self.page.evaluate('closePanel()')
        self.assertTrue(self.page.locator('#sb-module-alpha').is_checked())
        self.assertTrue(self.page.locator('#sb-module-beta').is_checked())

    def test_library_toggle_keeps_selected_module_and_updates_launchers(self):
        self.module_fixture()
        self.page.evaluate("currentSettingsTab='modules';showSettings(false)")
        self.page.locator('[data-ms-select="beta"]').click()
        self.page.locator('[data-module-enabled="beta"]').check()
        self.page.wait_for_function("moduleRows[1].enabled && document.querySelector('[data-module-open=beta].module-launch')")
        self.assertEqual(self.page.locator('.ms-module[aria-pressed=true]').get_attribute('data-ms-select'),'beta')
        self.assertTrue(self.page.locator('[data-module-enabled="beta"]').is_checked())
        self.page.locator('[data-ms-select="beta"]').focus();self.page.keyboard.press('Home')
        self.assertEqual(self.page.locator('.ms-module[aria-pressed=true]').get_attribute('data-ms-select'),'alpha')
        self.page.locator('#modules-filter').fill('Beta')
        self.assertEqual(self.page.locator('.ms-module').count(),1)
        self.assertEqual(self.page.locator('#ms-inspector h3').inner_text(),'Beta')

    def test_single_profile_menu_right_edge_opens_profile_settings(self):
        self.page.locator('#profile-card').click()
        self.assertEqual(self.page.locator('.profile-order-actions').count(),0)
        row=self.page.locator('.profile-menu-row.active');box=row.bounding_box()
        self.page.mouse.click(box['x']+box['width']-8,box['y']+box['height']/2)
        self.page.wait_for_selector('#profile-name')

    def test_multiple_profiles_have_no_dead_space_around_order_controls(self):
        self.page.evaluate("S.profileState.items.push({...S.profileState.items[0],id:'second',name:'Second'});")
        self.page.locator('#profile-card').click()
        self.page.evaluate("()=>{window.profileOpened=[];openProfileFromMenu=id=>profileOpened.push(id);}")
        row=self.page.locator('.profile-menu-row.active');box=row.bounding_box()
        for dx,dy in [(4,4),(box['width']-3,box['height']/2),(box['width']-36,4),(box['width']-36,box['height']-4)]:
            self.page.mouse.click(box['x']+dx,box['y']+dy)
        self.assertEqual(len(self.page.evaluate('profileOpened')),4)

    def test_stale_jev_row_is_not_usable_as_chat(self):
        self.page.evaluate("S.models.push({id:'or-typesafe/jev-1.13',name:'Jev 1.13',kind:'chat',caps:{text:true}});setCurrentModel('or-typesafe/jev-1.13')")
        self.assertEqual(self.page.evaluate("MemuModelPicker.kindOf(S.models.at(-1))"),'decisions')
        self.page.locator('#input').fill('Hello');self.page.locator('#btn-send').click()
        self.assertEqual(self.page.evaluate('S.messages.length'),0)

    def test_nontext_output_contract_overrides_stale_chat_kind_in_picker(self):
        result=self.page.evaluate("""()=>[
          {id:'opaque-image',kind:'chat',architecture:{output_modalities:['image']}},
          {id:'opaque-rerank',kind:'chat',output_modalities:['scores']},
          {id:'or-respan/span-01-lite:free',kind:'chat'},
          {id:'or-togethercomputer/tev1-4b-experimental',kind:'chat'},
          {id:'multimodal-chat',kind:'chat',output_modalities:['text','audio']}
        ].map(MemuModelPicker.kindOf)""")
        self.assertEqual(result,['image','rerank','decisions','decisions','chat'])

    def test_model_mentions_do_not_offer_specialized_models_or_stale_recents(self):
        self.page.evaluate("""()=>{
          S.models.push({id:'or-typesafe/jev-1.13',kind:'chat',name:'Jev 1.13'});
          MemuMentions._state.providers=[{id:'model',aliases:['модель'],label:['Модель','Model'],hint:['',''],client:true}];
          MemuMentions._state.providersAt=Date.now();
          localStorage.setItem('memu_mentions_recent',JSON.stringify([{provider:'model',ref:'or-typesafe/jev-1.13',model:'or-typesafe/jev-1.13',title:'Jev 1.13'}]));
        }""")
        self.page.locator('#input').fill('@модель:')
        self.page.wait_for_selector('#mention-menu:visible')
        self.assertNotIn('Jev',self.page.locator('#mention-menu').inner_text())
        self.assertIn('Claude',self.page.locator('#mention-menu').inner_text())
        self.page.locator('#input').fill('@')
        self.assertNotIn('Jev',self.page.locator('#mention-menu').inner_text())

if __name__=='__main__':unittest.main()
