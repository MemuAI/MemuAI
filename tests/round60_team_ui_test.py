import unittest
from tests import team_ui_test

class TeamUI(unittest.TestCase):
    setUpClass=classmethod(team_ui_test.TeamUI.setUpClass.__func__)
    tearDownClass=classmethod(team_ui_test.TeamUI.tearDownClass.__func__)
    setUp=team_ui_test.TeamUI.setUp
    tearDown=team_ui_test.TeamUI.tearDown
    create=team_ui_test.TeamUI.create

    def monitor(self):
        self.page.evaluate("""()=>{
          window.monitorMessage={teamRun:{id:'monitor-test',status:'running',members:[
            {id:'manager',name:'Manager',model:'demo-claude',status:'waiting'},
            {id:'qa',name:'Reviewer',role:'Evidence review',model:'demo-gemini',status:'working'}],
            tasks:[{id:'a',member_id:'qa',title:'Report A',report:'A'},{id:'b',member_id:'qa',title:'Report B',report:'B'}],events:[]}};
          MemuTeams.openMonitor(monitorMessage);
        }""")

    def test_monitor_preserves_the_open_report_by_task_id(self):
        self.monitor()
        self.page.locator('.tm-task details summary').nth(1).click()
        self.page.locator('[data-team-actor="qa"]').click()
        self.page.wait_for_function("document.activeElement?.dataset.teamActor==='qa'")
        opened=self.page.locator('.tm-task:has(details[open]) strong').all_text_contents()
        self.assertEqual(opened,['Report B'])

    def test_monitor_preserves_keyboard_focus_across_updates(self):
        self.monitor()
        self.page.locator('[data-team-actor="qa"]').focus()
        self.page.keyboard.press('Enter')
        self.page.wait_for_function("document.querySelector('[data-team-actor=qa]').getAttribute('aria-pressed')==='true'")
        self.assertEqual(self.page.evaluate('document.activeElement.dataset.teamActor'),'qa')

    def test_team_uses_text_models_even_when_current_chat_is_specialized(self):
        self.page.evaluate("S.models.push({id:'or-typesafe/jev-1.13',name:'Jev',kind:'chat'});localStorage.setItem('memu_model','or-typesafe/jev-1.13')")
        self.page.locator('#nav-team').click();self.page.locator('[data-tm-preset="research"]').click()
        self.assertEqual(self.page.locator('#tm-manager option[value="or-typesafe/jev-1.13"]').count(),0)
        self.assertNotEqual(self.page.locator('#tm-manager').input_value(),'or-typesafe/jev-1.13')

    def test_roster_and_monitor_are_readable_without_corner_icons(self):
        self.create()
        self.assertEqual(self.page.locator('.tm-person').count(),4)
        self.assertEqual(self.page.locator('.tm-avatar,.tm-roster-branch').count(),0)
        self.page.screenshot(path=str(self.output/'team-home-round60-dark.png'))
        self.page.evaluate("setTheme('light',false)")
        self.page.screenshot(path=str(self.output/'team-home-round60-light.png'))
        self.page.set_viewport_size({'width':390,'height':844})
        self.assertTrue(self.page.locator('#team-home').evaluate('n=>n.scrollWidth<=n.clientWidth+2'))
        self.page.screenshot(path=str(self.output/'team-home-round60-narrow.png'))
        self.page.locator('#tm-edit').click()
        self.assertTrue(self.page.locator('#panel').evaluate('n=>n.scrollWidth<=n.clientWidth+2'))
        self.page.screenshot(path=str(self.output/'team-editor-round60-narrow.png'))
        self.page.evaluate('closePanel()');self.monitor()
        self.assertTrue(self.page.locator('#panel').evaluate('n=>n.scrollWidth<=n.clientWidth+2'))
        self.page.screenshot(path=str(self.output/'team-monitor-round60-narrow.png'))

    def test_invalid_schedule_cannot_claim_the_previous_proposal(self):
        self.create();self.page.locator('#tm-start').click()
        value=self.page.evaluate("""async()=>{
          const old=extractSchedules;extractSchedules=async()=>{};
          try{return await MemuTeams.toolBridge({scheduleActions:{tasks:[{id:'old'}]}},{tool:'team_schedule',args:{}},captureChatSource(),{controller:new AbortController()});}
          finally{extractSchedules=old;}
        }""")
        self.assertEqual(value['status'],'invalid')

    def test_empty_delete_list_is_a_tool_error(self):
        self.create();self.page.locator('#tm-start').click()
        value=self.page.evaluate("MemuTeams.toolBridge({}, {tool:'team_delete',args:{paths:[]}},captureChatSource(),{controller:new AbortController()})")
        self.assertIn('non-empty',value['error'])

    def test_delete_reports_each_failure_without_hiding_successful_deletions(self):
        self.create();self.page.locator('#tm-start').click()
        value=self.page.evaluate("""async()=>{
          const source={...captureChatSource(),mode:'work',projectId:'fixture',accessMode:'full'},old=fetch;
          window.fetch=async(url,options)=>{
            if(String(url)==='/api/work/delete'){
              const body=JSON.parse(options.body);
              return new Response(JSON.stringify(body.path==='locked'?{error:'File locked'}:{path:body.path,deleted:true}),{status:body.path==='locked'?409:200});
            }
            return old(url,options);
          };
          try{return await MemuTeams.toolBridge({role:'assistant',content:''},{tool:'team_delete',args:{paths:['first','locked','last']}},source,{controller:new AbortController()});}
          finally{window.fetch=old;}
        }""")
        self.assertEqual(len(value['results']),3)
        self.assertIn('error',value)
        self.assertTrue(value['results'][2]['deleted'])

    def test_disappeared_dfy_task_releases_the_team_tool(self):
        self.create();self.page.locator('#tm-start').click()
        value=self.page.evaluate("""async()=>{
          let stopped=0;window.MemuDFY={start:async()=>({id:'gone',status:'running'}),cancel:async()=>{},watch:(id,fn)=>{setTimeout(()=>fn({id,status:'gone',gone:true}),0);return()=>stopped++;}};
          const result=await Promise.race([MemuTeams.toolBridge({role:'assistant',content:''},{tool:'team_dfy',args:{goal:'Check'}},captureChatSource(),{controller:new AbortController()}),new Promise(resolve=>setTimeout(()=>resolve({pending:true}),300))]);
          return {result,stopped};
        }""")
        self.assertNotIn('pending',value['result'])
        self.assertIn('error',value['result'])
        self.assertGreater(value['stopped'],0)

    def test_server_completion_cancels_a_stale_pending_tool(self):
        self.create();self.page.locator('#tm-start').click()
        self.page.evaluate("""()=>{
          window.pendingToolCancelled=0;
          window.MemuDFY={start:async()=>({id:'stale',status:'running'}),watch:()=>()=>{},cancel:async()=>pendingToolCancelled++,chatHtml:()=>'',hydrate:()=>{}};
          const old=fetch;window.fetch=async(url,options)=>{
            if(String(url)==='/api/pg/teams/run'){
              const enc=new TextEncoder();return new Response(new ReadableStream({start(c){
                const send=value=>c.enqueue(enc.encode('data: '+JSON.stringify(value)+'\\n\\n'));
                send({event:'team',type:'run_started',run_id:'stale-run',members:[]});
                send({event:'team_tool',run_id:'stale-run',request_id:'stale',tool:'team_dfy',member_id:'lead',args:{goal:'Check'}});
                setTimeout(()=>{send({event:'done',text:'Partial result with tool timeout',run:{id:'stale-run',status:'completed_with_warnings'}});c.close();},30);
              }}),{headers:{'Content-Type':'text/event-stream'}});
            }
            return old(url,options);
          };
        }""")
        self.page.locator('#input').fill('Check');self.page.locator('#btn-send').click()
        self.page.wait_for_function("S.messages.at(-1)?.content==='Partial result with tool timeout' && !S.messages.at(-1).streaming")
        self.assertEqual(self.page.evaluate('pendingToolCancelled'),1)

if __name__=='__main__':unittest.main()
