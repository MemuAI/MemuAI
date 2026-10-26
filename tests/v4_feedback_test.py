"""Regression cases reported after the first 4.0 installer."""
import asyncio
import json
import unittest
from collections import Counter
from app.team_runtime import TeamRun
from tests.team_runtime_test import definition, tool
from tests.team_ui_test import TeamUI

class FeedbackRuntime(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_advice_failure_does_not_cancel_other_workers(self):
        counts=Counter();advice=asyncio.Event()
        async def complete(model,messages,limit):
            counts[model]+=1
            if model=='manager-model':return {'text':json.dumps({'lead_id':'lead','brief':'Research','assignments':[
                {'member_id':'qa','task':'Verify'},{'member_id':'research','task':'Research'}]}) if counts[model]==1 else 'Research delivered; QA unavailable'}
            if model=='qa-model':raise RuntimeError('QA transport offline')
            if model=='research-model':await advice.wait();return {'text':'Actual research result'}
            if 'specialist stopped' in messages[0]['content']:advice.set();raise RuntimeError('Temporary lead transport error')
            self.assertIn('Actual research result',json.dumps(messages));return {'text':'Research delivered with QA limitation'}
        async def broker(*args):return {'approved':True}
        result=await asyncio.wait_for(TeamRun(definition(),complete=complete,tool=broker,emit=lambda e:None,guard=lambda:None,
            messages=[{'role':'user','content':'Research'}]).run(),5)
        self.assertEqual(result['run']['status'],'completed_with_warnings')
        self.assertEqual(next(t for t in result['run']['tasks'] if t['member_id']=='research')['status'],'done')

    async def test_failed_worker_wakes_lead_while_other_worker_is_still_busy(self):
        counts=Counter();recovered=asyncio.Event()
        async def complete(model,messages,limit):
            counts[model]+=1
            if model=='manager-model':
                return {'text':json.dumps({'lead_id':'lead','brief':'Research and verify','assignments':[
                    {'member_id':'qa','task':'Check source'},{'member_id':'research','task':'Long research'}]}) if counts[model]==1 else 'Recovered source and completed research'}
            if model=='qa-model':
                if counts[model]<3:raise RuntimeError('Provider unavailable')
                self.assertIn('Inspect actual state',json.dumps(messages));recovered.set();return {'text':'Recovered using a legitimate alternative'}
            if model=='research-model':
                await recovered.wait();return {'text':'Long research complete'}
            if 'specialist stopped' in messages[0]['content']:return {'text':'{"action":"retry","guidance":"Use the available alternate source"}'}
            return {'text':'Checked both results'}
        async def broker(*args):return {'approved':True}
        result=await asyncio.wait_for(TeamRun(definition(),complete=complete,tool=broker,emit=lambda e:None,guard=lambda:None,
            messages=[{'role':'user','content':'Research'}]).run(),5)
        self.assertTrue(recovered.is_set())
        self.assertEqual(len(result['run']['tasks']),3)
        self.assertEqual(result['run']['tasks'][-1]['status'],'done')

    def test_long_context_retains_assignment_and_latest_tool_evidence(self):
        run=TeamRun(definition(),complete=None,tool=None,emit=lambda e:None,guard=lambda:None,messages=[])
        messages=[{'role':'system','content':'Worker rules'},
                  {'role':'user','content':"Manager's brief:\nCompare prices\n\nYour assignment:\nSearch only Russian stores"}]
        messages += [{'role':'user','content':'Old evidence '+('x'*12000)} for _ in range(20)]
        messages.append({'role':'user','content':'Latest source: https://example.test/verified'})
        bound=run._bound(messages)
        self.assertIn('Search only Russian stores',bound[1]['content'])
        self.assertIn('https://example.test/verified',bound[-1]['content'])
        self.assertLess(sum(len(m['content']) for m in bound),run.budget['context_chars'])

    async def test_manager_recruits_then_revises_plan_before_assignments(self):
        counts=Counter();approved=False;plans=[];seen=[]
        async def complete(model,messages,limit):
            counts[model]+=1;seen.append((model,json.dumps(messages)))
            if model=='manager-model' and not approved:
                return {'text':json.dumps({'plan':'Plan '+str(counts[model]),'brief':'Compare prices in the requested country',
                    'lead_id':'leader','members':[{'id':'leader','name':'Lead','role':'Lead','model':'lead-model','is_lead':True},
                    {'id':'searcher','name':'Search','role':'Find prices','model':'research-model'},
                    {'id':'reviewer','name':'Review','role':'Check evidence','model':'qa-model'}],
                    'assignments':[{'member_id':'searcher','task':'Find prices'},{'member_id':'reviewer','task':'Check sources','reviewer':True}]})}
            self.assertTrue(approved)
            return {'text':'Verified result'}
        async def broker(actor,name,args):
            nonlocal approved
            self.assertEqual(name,'team_confirm');plans.append(args['plan'])
            if len(plans)==1:return {'feedback':'Use stores in Russia'}
            approved=True;return {'approved':True}
        team=definition();team['members']=[]
        result=await TeamRun(team,complete=complete,tool=broker,emit=lambda e:None,guard=lambda:None,
            messages=[{'role':'user','content':'Compare prices'}],available_models=['lead-model','research-model','qa-model']).run()
        self.assertEqual(plans,['Plan 1','Plan 2'])
        self.assertIn('Use stores in Russia',seen[1][1])
        self.assertEqual([t['member_id'] for t in result['run']['tasks']],['searcher','reviewer'])
        self.assertEqual(result['run']['status'],'completed')

    async def test_reviewer_waits_for_approved_repair_with_single_model_slot(self):
        counts=Counter();order=[]
        async def complete(model,messages,limit):
            counts[model]+=1
            if model=='manager-model':
                if counts[model]==1:return {'text':json.dumps({'lead_id':'lead','brief':'Compare prices','assignments':[
                    {'member_id':'research','task':'Find source'},{'member_id':'qa','task':'Review source','reviewer':True}]})}
                return {'text':'Final checked result'}
            if model=='research-model':order.append('worker');return {'text':'Source fixed' if counts[model]>1 else 'Source needs correction'}
            if model=='qa-model':
                if counts[model]==1:return {'text':tool('team_request_revision',member_id='research',task='Correct source URL',title='Repair')}
                if counts[model]==2:return {'text':tool('team_wait')}
                self.assertIn('Source fixed',json.dumps(messages));order.append('reviewed');return {'text':'Verified corrected source'}
            if 'repair request' in messages[0]['content']:return {'text':'{"approved":true}'}
            order.append('lead');return {'text':'Integrated and checked'}
        async def broker(*args):return {'approved':True}
        team=definition();team['budget']['max_parallel']=1
        result=await asyncio.wait_for(TeamRun(team,complete=complete,tool=broker,emit=lambda e:None,guard=lambda:None,
            messages=[{'role':'user','content':'Compare prices'}]).run(),5)
        self.assertEqual(order,['worker','worker','reviewed','lead'])
        self.assertEqual(result['run']['status'],'completed')

    async def test_approval_precedes_work_and_http_error_is_recoverable(self):
        counts=Counter(); calls=[]; approved=False; events=[]
        async def complete(model,messages,limit):
            counts[model]+=1
            if model=='manager-model':
                return {'text':json.dumps({'lead_id':'lead','plan':'Compare exact products and stores.','brief':'Compare prices'}) if counts[model]==1 else 'Verified alternative source.'}
            self.assertTrue(approved)
            if counts[model]==1:return {'text':tool('web_fetch',url='https://example.test/blocked')}
            self.assertIn('HTTP 401',json.dumps(messages))
            return {'text':'Used another source; inaccessible page excluded.'}
        async def broker(actor,name,args):
            nonlocal approved
            calls.append(name)
            if name=='team_confirm':approved=True;return {'approved':True}
            return {'error':'HTTP 401'}
        result=await TeamRun(definition(),complete=complete,tool=broker,emit=events.append,guard=lambda:None,messages=[{'role':'user','content':'Compare prices'}]).run()
        self.assertEqual(calls[0],'team_confirm')
        self.assertEqual(result['run']['status'],'completed')
        self.assertTrue(any(e.get('type')=='tool' and e.get('error')=='HTTP 401' for e in events))

    async def test_declining_plan_never_calls_a_worker(self):
        calls=[]
        async def complete(model,messages,limit):
            calls.append(model)
            return {'text':'{"lead_id":"lead","brief":"Work","plan":"Proposed plan"}'}
        async def broker(*args):return {'approved':False}
        result=await TeamRun(definition(),complete=complete,tool=broker,emit=lambda e:None,guard=lambda:None,messages=[{'role':'user','content':'Task'}]).run()
        self.assertEqual(calls,['manager-model']);self.assertEqual(result['run']['status'],'cancelled')

class FeedbackUI(unittest.TestCase):
    setUpClass=classmethod(TeamUI.setUpClass.__func__)
    tearDownClass=classmethod(TeamUI.tearDownClass.__func__)
    setUp=TeamUI.setUp
    tearDown=TeamUI.tearDown
    create=TeamUI.create

    def test_sidebar_restores_hidden_team_and_chat_header_fits(self):
        self.page.locator('#tm-nav-customize').click()
        self.page.locator('#tm-nav-team-visible').uncheck()
        self.page.wait_for_function("document.querySelector('#nav-team').hidden")
        self.assertTrue(self.page.locator('#tm-nav-customize').is_visible())
        self.page.locator('#tm-nav-team-visible').check()
        self.page.wait_for_function("!document.querySelector('#nav-team').hidden")
        self.page.evaluate('closePanel()')
        self.create();self.page.locator('#tm-start').click()
        self.page.wait_for_selector('#team-chat-bar:visible')
        box=self.page.locator('#team-chat-bar').bounding_box()
        chat=self.page.locator('#chat').bounding_box()
        self.assertGreaterEqual(box['x'],chat['x'])
        self.assertLessEqual(box['x']+box['width'],chat['x']+chat['width']+1)
        self.page.screenshot(path=str(self.output/'team-chat-header-feedback.png'))

    def test_nonchat_model_is_rejected_before_send_and_regeneration(self):
        self.page.evaluate("S.models.push({id:'decision-only',kind:'decisions',name:'Decision only'});localStorage.setItem('memu_model','decision-only')")
        self.page.locator('#input').fill('Hello');self.page.locator('#btn-send').click()
        self.assertEqual(self.page.evaluate('S.messages.length'),0)
        result=self.page.evaluate("async()=>{const a={role:'assistant',content:'',streaming:true};await generate(a,'decision-only','Hello',[]);return a;}")
        self.assertFalse(result['streaming']);self.assertIn('текстового чата',result['error'])

    def test_profile_edge_click_survives_held_pointer(self):
        self.page.evaluate("document.documentElement.dataset.motion='full'")
        box=self.page.locator('#profile-card').bounding_box()
        self.page.mouse.move(box['x']+1,box['y']+box['height']/2)
        self.page.mouse.down();self.page.wait_for_timeout(120);self.page.mouse.up()
        self.assertTrue(self.page.locator('#profile-menu-pop').is_visible())

    def test_team_tool_error_event_does_not_abort_stream(self):
        self.create();self.page.locator('#tm-start').click()
        self.page.evaluate('''()=>{const old=fetch;window.fetch=async(url,opts)=>{const response=await old(url,opts);if(String(url)!='/api/pg/teams/run')return response;const reader=response.body.getReader(),encoder=new TextEncoder();return new Response(new ReadableStream({async start(c){c.enqueue(encoder.encode('data: '+JSON.stringify({event:'team',type:'tool',member_id:'lead',tool:'web_fetch',error:'HTTP 401',status:'error'})+'\\n\\n'));while(true){const chunk=await reader.read();if(chunk.done)break;c.enqueue(chunk.value);}c.close();}}),{headers:{'Content-Type':'text/event-stream'}});}}''')
        self.page.locator('#input').fill('Compare prices');self.page.locator('#btn-send').click()
        self.page.wait_for_function("S.messages.at(-1)?.content==='Проверенный результат команды.' && !S.messages.at(-1).streaming")
        self.assertFalse(self.page.evaluate("!!S.messages.at(-1).error"))
        self.page.locator('[data-team-monitor]').click()
        self.page.locator('[data-team-actor="lead"]').click()
        self.page.wait_for_function("document.querySelector('[data-team-actor=lead]').getAttribute('aria-pressed')==='true'")

if __name__=='__main__':unittest.main()
