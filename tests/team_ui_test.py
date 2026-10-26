"""Team editor and real chat integration in an isolated, offline Chromium."""
from __future__ import annotations

import json
import os
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class TeamUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        from tests.appearance_fixture import build_fixture
        from app.teams import presets
        cls.output = ROOT / "build" / "v4-team-ui"
        cls.fixture = build_fixture(cls.output)
        cls.presets = presets()
        cls.pw = sync_playwright().start()
        chrome = Path(os.environ.get("MEMU_TEST_CHROMIUM", r"C:\Program Files\Google\Chrome\Application\chrome.exe"))
        if not chrome.is_file():
            cls.pw.stop()
            raise unittest.SkipTest("Chromium unavailable")
        cls.browser = cls.pw.chromium.launch(executable_path=str(chrome), headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width":1440,"height":1000}, reduced_motion="reduce")
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.route("http://**/*", lambda route: route.abort())
        self.page.route("https://**/*", lambda route: route.abort())
        self.page.goto(self.fixture.as_uri())
        self.page.wait_for_function("document.body.classList.contains('boot-ready')")
        self.page.evaluate("""presets => {
          window.teamStore=[]; window.teamCalls=[]; window.teamAnswers=[];
          const old=window.fetch;
          window.fetch=async (url,options={})=>{
            const path=String(url).split('?')[0],body=options.body?JSON.parse(options.body):{};
            const answer=value=>new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}});
            if(path==='/api/pg/teams') {
              if(options.method==='POST') {const team={...body.team,id:body.team.id || 'test-team'};teamStore=teamStore.filter(t=>t.id!==team.id);teamStore.push(team);return answer({team});}
              return answer({teams:teamStore,presets,profile_id:'default'});
            }
            if(path.startsWith('/api/pg/teams/runs/')) {
              teamAnswers.push({path,body}); if(window.teamReply) {teamReply();window.teamReply=null;}return answer({ok:true});
            }
            if(path==='/api/pg/teams/run') {
              teamCalls.push(body);
              const encoder=new TextEncoder();
              const stream=new ReadableStream({async start(c){
                const send=v=>c.enqueue(encoder.encode('data: '+JSON.stringify(v)+'\\n\\n'));
                send({event:'team',type:'run_started',run_id:'run-test',team_name:'Test team',members:[{id:'manager',name:'Менеджер',model:'demo-claude',status:'thinking'},{id:'lead',name:'Лидер',role:'Тимлид',model:'demo-gemini',status:'working'}]});
                send({event:'team',type:'phase',run_id:'run-test',phase:'working'});
                send({event:'team',type:'task',run_id:'run-test',task_id:'task-1',member_id:'lead',title:'Проверить результат',status:'running'});
                await new Promise(resolve=>{window.teamReply=resolve;send({event:'team_tool',run_id:'run-test',request_id:'req-1',member_id:'lead',tool:'current_time',args:{}});});
                send({event:'team',type:'usage',run_id:'run-test',calls:4,usage:{prompt_tokens:100,completion_tokens:50}});
                send({event:'delta',text:'Проверенный результат команды.'});
                send({event:'done',text:'Проверенный результат команды.',run:{id:'run-test',status:'completed',phase:'finished',calls:4,usage:{prompt_tokens:100,completion_tokens:50}}});
                c.enqueue(encoder.encode('data: [DONE]\\n\\n'));c.close();
              }});
              return new Response(stream,{headers:{'Content-Type':'text/event-stream'}});
            }
            if(path==='/api/pg/tools') {teamCalls.push({tool:body.tool});return answer({tool:body.tool,text:'2026-10-24'});}
            return old(url,options);
          };
        }""", self.presets)

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def create(self):
        self.page.locator("#nav-team").click()
        self.page.locator('[data-tm-preset="research"]').click()
        self.page.locator("#tm-name").fill("Команда проверки")
        self.page.locator("#tm-save").click()
        self.page.wait_for_selector(".tm-team-detail h2:text('Команда проверки')")

    def test_editor_and_chat_run_share_history_and_tool_executor(self):
        self.create()
        self.assertEqual(self.page.locator(".tm-person").count(),4)
        self.page.locator("#tm-start").click()
        self.assertTrue(self.page.locator("#team-home").is_hidden())
        self.assertTrue(self.page.locator("#team-chat-bar").is_visible())
        self.page.locator("#input").fill("Проверь результат")
        self.page.locator("#btn-send").click()
        self.page.wait_for_function("S.messages.at(-1)?.content === 'Проверенный результат команды.' && !S.messages.at(-1).streaming")
        self.assertEqual(self.page.evaluate("teamAnswers.length"),1)
        self.assertEqual(self.page.evaluate("teamAnswers[0].body.result.tool"),"current_time")
        self.assertEqual(self.page.evaluate("currentChat().chatCaps.team_id"),"test-team")
        self.assertEqual(self.page.evaluate("currentChat().messages.at(-1).teamRun.calls"),4)
        self.assertIn("team_tool_protocol",self.page.evaluate("teamCalls[0].tools_prompt"))
        self.page.locator("[data-team-monitor]").click()
        self.assertTrue(self.page.locator("#tm-monitor").is_visible())
        self.assertTrue(self.page.locator(".tm-live-person.manager").is_visible())
        self.assertIn("150",self.page.locator(".tm-monitor-summary").inner_text())
        self.page.screenshot(path=str(self.output / "team-run-dark.png"))
        self.page.keyboard.press("Escape")
        self.page.locator("#btn-new").click()
        self.assertFalse(self.page.evaluate("!!S.chatCaps.team_id"))
        self.page.evaluate("openChat(S.chats[0].id)")
        self.assertTrue(self.page.locator("#team-chat-bar").is_visible())

    def test_responsive_editor_preserves_typed_members(self):
        self.create()
        self.page.locator("#tm-edit").click()
        self.page.locator('[data-tm-member] [data-field="role"]').first.fill("Проверка точности")
        self.page.locator("#tm-add-member").click()
        self.assertEqual(self.page.locator('[data-tm-member] [data-field="role"]').first.input_value(),"Проверка точности")
        self.assertEqual(self.page.locator("[data-tm-member]").count(),4)
        self.page.set_viewport_size({"width":390,"height":844})
        self.assertLessEqual(self.page.locator("#tm-editor").evaluate("n=>n.getBoundingClientRect().right"),390)
        self.assertEqual(self.page.evaluate("document.querySelector('#panel').scrollWidth <= document.querySelector('#panel').clientWidth+2"),True)
        self.page.screenshot(path=str(self.output / "team-editor-narrow.png"))
        self.page.keyboard.press("Escape")
        self.page.set_viewport_size({"width":1440,"height":1000})
        self.page.evaluate("setTheme('light',false)")
        self.page.screenshot(path=str(self.output / "team-home-light.png"))

    def test_hidden_shortcut_remains_accessible_and_home_navigation_exits(self):
        self.create()
        self.page.locator("#tm-sidebar").uncheck()
        self.assertTrue(self.page.locator("#nav-team").is_hidden())
        self.page.evaluate("MemuTeams.open()")
        self.assertTrue(self.page.locator("#team-home").is_visible())
        self.page.locator("#nav-chat").click()
        self.assertTrue(self.page.locator("#team-home").is_hidden())
        self.assertTrue(self.page.locator("#chat-view-wrap").is_visible())

    def test_dfy_approval_metadata_goes_to_manager_without_secure_values(self):
        self.create();self.page.locator("#tm-start").click()
        result=self.page.evaluate("""async()=>{
          const source=captureChatSource(),job={controller:new AbortController()};
          const assistant={role:'assistant',content:'',teamRun:{id:'run-approval',events:[],members:[]}};
          const requests=[],answers=[];const old=fetch;
          window.fetch=(url,opts)=>{
            if(String(url).endsWith('/review')) {requests.push(JSON.parse(opts.body));return Promise.resolve(new Response(JSON.stringify({action:'approve'})));}
            return old(url,opts);
          };
          let emit;
          window.MemuDFY={
            start:async()=>({id:'dfy-test',status:'running'}),cancel:async()=>({}),
            watch:(id,callback)=>{emit=callback;setTimeout(()=>callback({id,status:'waiting_user',question:{id:'secure',kind:'secret',password:'SECRET_VALUE'}}),0);
              setTimeout(()=>callback({id,status:'waiting_user',question:{id:'confirm-1',kind:'confirm',prompt:'Open the explicitly requested page',password:'SECRET_VALUE',code:'SECRET_CODE'}}),10);return ()=>{};},
            answer:async(id,value)=>{answers.push(value);setTimeout(()=>emit({id,status:'done',result:'Completed'}),0);return {};},
          };
          const value=await MemuTeams.toolBridge(assistant,{tool:'team_dfy',member_id:S.chatCaps.team_snapshot.members[0].id,args:{goal:'Open the requested page'}},source,job);
          return {requests,answers,value};
        }""")
        self.assertEqual(len(result["requests"]),1)
        self.assertEqual(result["answers"],[{"question_id":"confirm-1","approve":True}])
        self.assertNotIn("SECRET_VALUE",json.dumps(result))
        self.assertNotIn("SECRET_CODE",json.dumps(result))
        self.assertEqual(result["value"]["status"],"done")

if __name__ == "__main__":
    unittest.main()
