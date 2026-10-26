import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import teams
from app import pgdata, teams_api
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.team_runtime import TeamRun, TeamLimit
from tests.team_runtime_test import definition, tool

class Definitions(unittest.TestCase):
    def test_strict_roster_rejects_silently_lost_members(self):
        base=definition()
        for members in [[*base['members'], *[{**base['members'][0],'id':f'extra-{i}'} for i in range(4)]], [*base['members'],None], {'lead':{}}]:
            with self.subTest(members=members),self.assertRaises(ValueError):
                teams.normalize({**base,'members':members})

    def test_oversized_identifiers_do_not_overwrite_their_prefix(self):
        for raw in [{**definition(),'id':'a'*81},{**definition(),'members':[{**definition()['members'][0],'id':'a'*81}]}]:
            with self.subTest(raw=raw),self.assertRaises(ValueError):teams.normalize(raw)

class Runtime(unittest.IsolatedAsyncioTestCase):
    def make(self,complete=None,**kwargs):
        async def default(*_):return {'text':'Result'}
        async def broker(*_):return {'ok':True}
        return TeamRun(definition(),complete=complete or default,tool=broker,emit=lambda _:None,guard=lambda:None,
                       messages=[{'role':'user','content':'Task'}],require_confirmation=False,**kwargs)

    async def test_approval_cache_is_bound_to_actor_and_exact_proposal(self):
        count=0
        async def complete(*_):
            nonlocal count
            count+=1
            return {'text':'{"recommendation":"approve","action":"approve","confidence":1,"reason":"Explicitly requested"}'}
        run=self.make(complete);run.lead_id='lead'
        await run.review_approval('qa','dfy','same',{'action':'open','url':'https://example.com/a'})
        await run.review_approval('qa','dfy','same',{'action':'open','url':'https://example.com/a'})
        self.assertEqual(count,2)
        await run.review_approval('qa','dfy','same',{'action':'delete','url':'https://example.com/b'})
        await run.review_approval('research','dfy','same',{'action':'delete','url':'https://example.com/b'})
        self.assertEqual(count,6)

    def test_multipart_context_respects_budget_for_every_part(self):
        run=self.make();run.budget['context_chars']=16000
        messages=[{'role':'system','content':'System'},{'role':'user','content':[
            *[{'type':'text','text':'x'*16000} for _ in range(5)],
            *[{'type':'image_url','image_url':{'url':'data:image/png;base64,AAAA'}} for _ in range(4)]]}]
        result=run._bound(messages)
        weight=sum(len(m['content']) if isinstance(m['content'],str) else sum(len(p.get('text','')) if p['type']=='text' else 4096 for p in m['content']) for m in result)
        self.assertLessEqual(weight,16000)

    async def test_long_manager_context_cannot_remove_response_schema(self):
        seen=[]
        async def complete(model,messages,_):
            seen.append(messages[0]['content'])
            return {'text':'{"lead_id":"lead","brief":"Task"}'}
        run=self.make(complete,manager_context='personal '*20000)
        await run._brief()
        self.assertIn('Return JSON only:',seen[0])
        self.assertIn('assignments',seen[0])

    async def test_estimated_usage_counts_each_call_once(self):
        async def complete(*_):return {'text':'Result','usage':{'estimated':True}}
        run=self.make(complete)
        await run._complete('manager',[{'role':'user','content':'Hello'}])
        self.assertEqual(run.usage['estimated_calls'],1)

    async def test_boolean_token_counts_cannot_suppress_usage_estimation(self):
        async def complete(*_):return {'text':'Result','usage':{'prompt_tokens':True,'completion_tokens':False}}
        run=self.make(complete)
        await run._complete('manager',[{'role':'user','content':'Hello'}])
        self.assertGreater(run.usage.get('estimated_prompt_tokens',0),0)

    async def test_tool_only_manager_final_is_not_a_completed_empty_result(self):
        calls=0
        async def complete(model,*_):
            nonlocal calls
            calls+=1
            return {'text':'{"lead_id":"lead","brief":"Task"}' if calls==1 else 'Checked' if model=='lead-model' else tool('current_time')}
        run=self.make(complete)
        with self.assertRaisesRegex(RuntimeError,'[Ee]mpty|пуст'):
            await run.run()
        self.assertEqual(run.status,'error')

    async def test_truncated_member_report_is_explicit(self):
        async def complete(*_):return {'text':'a'*20000+'IMPORTANT_FINAL_EVIDENCE'}
        run=self.make(complete);run.lead_id='lead'
        report=await run._loop('qa','Do the work')
        self.assertIn('IMPORTANT_FINAL_EVIDENCE',report)
        self.assertIn('omitted',report.lower())

    async def test_stale_run_cannot_approve_automation(self):
        run=self.make();run.lead_id='lead';run.status='cancelled'
        result=await run.review_approval('qa','dfy','action',{'action':'delete'})
        self.assertEqual(result['action'],'user')
        self.assertEqual(run.calls,0)

    async def test_incomplete_automation_context_cannot_be_auto_approved(self):
        run=self.make();run.lead_id='lead'
        for proposal in [{},{'prompt':'a'*1700},{'password':'secret'}]:
            self.assertEqual((await run.review_approval('qa','dfy','req',proposal))['action'],'user')
        self.assertEqual(run.calls,0)

    async def test_boolean_confidence_is_not_numeric_authorization(self):
        async def complete(*_):return {'text':'{"action":"approve","confidence":true}'}
        run=self.make(complete);run.lead_id='lead'
        self.assertEqual((await run.review_approval('qa','dfy','req',{'action':'open'}))['action'],'user')

    async def test_final_external_tool_request_is_a_visible_warning(self):
        calls=0;events=[]
        async def complete(model,*_):
            nonlocal calls
            calls+=1
            return {'text':'{"lead_id":"lead","brief":"Task"}' if calls==1 else 'Checked' if model=='lead-model' else 'Result.\n```memuai-command\n{"command":"echo unsafe"}\n```'}
        run=self.make(complete);run.emit=events.append
        result=await run.run()
        self.assertEqual(result['text'].strip(),'Result.')
        self.assertTrue(any(e.get('type')=='warning' for e in events))

    async def test_mixed_valid_and_invalid_tool_calls_return_an_error_for_the_missing_action(self):
        calls=0;seen=[]
        async def complete(model,messages,_):
            nonlocal calls
            calls+=1;seen.append(json.dumps(messages))
            return {'text':tool('current_time')+'\n```memuai-tool\n[not valid json]\n```' if calls==1 else 'Verified'}
        run=self.make(complete);run.lead_id='lead'
        await run._loop('qa','Task')
        self.assertIn('invalid or incomplete protocol block',seen[-1])

class API(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.patcher=patch.object(pgdata,'MEMU_DIR',Path(self.tmp.name));self.patcher.start()
        pgdata.save_profiles({'items':[{'id':'default','name':'Test'}],'active_id':'default'})
        teams.save(definition(),'default')

    def tearDown(self):
        self.patcher.stop();self.tmp.cleanup()

    def test_cancel_before_stream_task_starts_is_remembered(self):
        app=FastAPI();teams_api.register(app)
        entry=teams_api.Live('default','chat','pending');app.state.team_runs['pending']=entry
        response=TestClient(app).post('/api/pg/teams/runs/pending/cancel',json={'profile_id':'default'})
        self.assertEqual(response.status_code,200)
        self.assertTrue(entry.cancelled)

    def test_truncated_upstream_answer_is_not_accepted_as_a_report(self):
        async def stream(*_):
            yield ('data: '+json.dumps({'choices':[{'delta':{'content':'{"lead_id":"lead","brief":"Task"}'},'finish_reason':'length'}]})+'\n\n').encode()
            yield b'data: [DONE]\n\n'
        app=FastAPI();teams_api.register(app,proxy_sse=stream)
        response=TestClient(app).post('/api/pg/teams/run',json={'team_id':'test-team','chat_id':'test','messages':[{'role':'user','content':'Task'}]})
        self.assertIn('truncated or blocked',response.text)
        self.assertNotIn('"status": "completed"',response.text)

if __name__=='__main__':unittest.main()
