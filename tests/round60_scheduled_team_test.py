import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch, Mock
from app import schedule_runtime, pgdata, teams
from app.state import state
from app.team_runtime import TeamContextChanged

class ScheduledTeam(unittest.IsolatedAsyncioTestCase):
    async def run_case(self, behavior, status='completed', rows=None, active=None, task=None):
        active = active if active is not None else {'id':'default'}
        spec={'id':'team', 'name':'Team', 'manager_model':'text-manager', 'members':[{'id':'lead','name':'Lead','role':'Lead','model':'text-worker'}]}
        save=Mock()
        class Run:
            def __init__(self, team, **kwargs):
                self.kwargs=kwargs;self.id='scheduled-run'
            def snapshot(self):return {'id':self.id,'status':status}
            async def run(self):return await behavior(self)
        with patch.object(pgdata,'load_profiles',side_effect=lambda:{'active_id':active['id']}),patch.object(teams,'load',return_value=[spec]),patch.object(schedule_runtime,'_lang',return_value='en'),patch.object(state,'find_model',return_value=None),patch.object(state,'enabled_models',return_value=rows or []),patch('app.team_runtime.TeamRun',Run),patch('app.teams_api._save_run',save):
            result=await schedule_runtime._team({'team_id':'team','prompt':'Research this',**(task or {})})
        return result,save

    async def test_direct_team_action_expands_scheduling_variables(self):
        async def behavior(run):
            self.assertEqual(run.kwargs['messages'][-1]['content'], 'Digest: 7')
            return {'text':'Done','run':run.snapshot()}
        result,_=await self.run_case(behavior,task={'prompt':'{{task_name}}: {{run_count}}','name':'Digest','run_count':7})
        self.assertTrue(result[0])

    async def test_profile_guard_and_failed_run_are_persisted(self):
        active={'id':'default'}
        async def behavior(run):
            run.kwargs['guard']();active['id']='other'
            with self.assertRaises(TeamContextChanged):run.kwargs['guard']()
            raise TeamContextChanged('Profile changed')
        result,save=await self.run_case(behavior,active=active,status='cancelled')
        self.assertFalse(result[0]);self.assertEqual(result[2]['run_id'],'scheduled-run')
        self.assertEqual(save.call_args.args[0],'default')

    async def test_recruitment_excludes_decision_models_and_tools_have_help(self):
        async def behavior(run):
            self.assertEqual(run.kwargs['available_models'],['ordinary-chat'])
            self.assertIn('web_search',run.kwargs['tools_prompt'])
            self.assertIn('current_time',run.kwargs['tools_prompt'])
            return {'text':'Done','run':run.snapshot()}
        result,_=await self.run_case(behavior,rows=[SimpleNamespace(id='or-typesafe/jev-1.13'),SimpleNamespace(id='ordinary-chat')])
        self.assertTrue(result[0])

    async def test_specialized_member_never_reaches_transport(self):
        async def behavior(run):
            await run.kwargs['complete']('or-typesafe/jev-1.13',[],100)
        with patch('app.chat.chat_service.stream_completion') as transport:
            result,_=await self.run_case(behavior)
        self.assertFalse(result[0]);transport.assert_not_called()

    async def test_sse_errors_missing_finish_and_truncation_never_become_success(self):
        for chunks in [[{'error':{'message':'Denied'}}],[{'choices':[{'delta':{'content':'partial'}}]}],[{'choices':[{'delta':{'content':'partial'},'finish_reason':'length'}]}],[{'choices':[{'delta':{'content':'x'*10000},'finish_reason':'stop'}]}]]:
            async def stream(*args,**kwargs):
                for chunk in chunks:yield chunk
            async def behavior(run):
                answer=await run.kwargs['complete']('ordinary-chat',[],8)
                return {'text':answer['text'],'run':run.snapshot()}
            with self.subTest(chunks=str(chunks)[:60]),patch('app.chat.chat_service.stream_completion',stream):
                result,save=await self.run_case(behavior,status='error')
                self.assertFalse(result[0]);save.assert_called_once()

    async def test_only_first_choice_is_used_and_usage_is_preserved(self):
        async def stream(*args,**kwargs):
            yield {'choices':[{'delta':{'content':'First'},'finish_reason':'stop'},{'delta':{'content':'Second'},'finish_reason':'stop'}],'usage':{'prompt_tokens':20}}
        async def behavior(run):
            answer=await run.kwargs['complete']('ordinary-chat',[],100)
            self.assertEqual(answer['text'],'First');self.assertEqual(answer['usage']['prompt_tokens'],20)
            return {'text':answer['text'],'run':run.snapshot()}
        with patch('app.chat.chat_service.stream_completion',stream):result,_=await self.run_case(behavior)
        self.assertTrue(result[0])

    async def test_limited_result_is_not_reported_as_success(self):
        async def behavior(run):return {'text':'Only partial work','run':run.snapshot()}
        result,save=await self.run_case(behavior,status='limited')
        self.assertFalse(result[0]);save.assert_called_once()

if __name__=='__main__':unittest.main()
