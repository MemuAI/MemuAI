"""Offline collaboration acceptance tests with actual async scheduling and storage."""
from __future__ import annotations

import asyncio
from collections import Counter
import copy
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import pgdata, teams, teams_api
from app.team_runtime import TeamContextChanged, TeamRun, external_calls

def definition():
    return teams.normalize({"id":"test-team","name":"Team","manager_model":"manager-model",
        "members":[{"id":"lead","name":"Lead","model":"lead-model","role":"Integration","is_lead":True},
                   {"id":"qa","name":"QA","model":"qa-model","role":"Verify"},
                   {"id":"research","name":"Research","model":"research-model","role":"Research"}]})

def tool(name, **args):
    return "```memuai-tool\n" + json.dumps([{"tool":name,"args":args}]) + "\n```"

class Runtime(unittest.IsolatedAsyncioTestCase):
    def run_for(self, complete, **kwargs):
        self.events=[]
        async def broker(actor,name,args):
            self.broker_calls.append((actor,name,args))
            return {"tool":name,"ok":True,"result":"actual evidence"}
        self.broker_calls=[]
        return TeamRun(definition(),complete=complete,tool=kwargs.pop("tool",broker),emit=self.events.append,
                       guard=kwargs.pop("guard",lambda:None),messages=[{"role":"user","content":"Build feature"}],
                       require_confirmation=False,manager_context="PRIVATE_PERSONAL_MEMORY",tools_prompt="Shared tool instructions",**kwargs)

    async def test_parallel_delegation_private_manager_context_and_final_review(self):
        calls=Counter(); captured=[]; active=0; peak=0
        async def complete(model,messages,max_tokens):
            nonlocal active,peak
            actor=re.search(r"TEAM_ACTOR:([^\]]+)",messages[0]["content"])[1]
            captured.append((actor,copy.deepcopy(messages)));calls[actor]+=1
            if actor=="manager":
                text=json.dumps({"lead_id":"lead","brief":"Implement the requested feature and verify it"}) if calls[actor]==1 else "Delivered and verified."
            elif actor=="lead":
                if calls[actor]==1:
                    text=tool("team_delegate",member_id="qa",title="Test",task="Verify acceptance in tests")+"\n"+tool("team_delegate",member_id="research",title="Sources",task="Check evidence")
                elif calls[actor]==2:text=tool("team_wait")
                elif calls[actor]==3:text="Lead implementation complete."
                else:text="Integrated both reports and checked acceptance."
            else:
                active+=1;peak=max(peak,active);await asyncio.sleep(.02);active-=1
                text="Verified actual evidence."
            return {"text":text,"usage":{"prompt_tokens":100,"completion_tokens":20}}
        runtime=self.run_for(complete)
        result=await runtime.run()
        self.assertEqual(result["run"]["status"],"completed")
        self.assertEqual(result["text"],"Delivered and verified.")
        self.assertEqual(peak,2)
        self.assertEqual(len(result["run"]["tasks"]),2)
        self.assertGreaterEqual(calls["lead"],4)
        for actor,messages in captured:
            if actor!="manager":self.assertNotIn("PRIVATE_PERSONAL_MEMORY",json.dumps(messages))
        self.assertNotIn("PRIVATE_PERSONAL_MEMORY",json.dumps(result["run"]))
        self.assertTrue(any(e.get("phase")=="reviewing" for e in self.events))
        self.assertEqual(result["run"]["usage"]["prompt_tokens"],sum(calls.values())*100)

    async def test_manager_question_uses_existing_form_schema(self):
        counts=Counter()
        async def complete(model,messages,max_tokens):
            counts[model]+=1
            if model=="manager-model":
                if counts[model]==1:return {"text":json.dumps({"lead_id":"lead","brief":"Choose a delivery date"})}
                if counts[model]==2:return {"text":'{"action":"user"}'}
                return {"text":"Scheduled for the chosen date."}
            if counts[model]==1:return {"text":tool("team_ask_manager",question="Which day?",options=["Monday","Tuesday"])}
            return {"text":"Used the actual user choice."}
        runtime=self.run_for(complete)
        result=await runtime.run()
        question=next(row for row in self.broker_calls if row[1]=="ask_user")
        self.assertEqual(question[0],"manager")
        self.assertEqual(question[2]["question"],"Which day?")
        self.assertEqual(question[2]["fields"][0]["type"],"choice")
        self.assertEqual(result["run"]["status"],"completed")

    async def test_member_failure_is_visible_and_does_not_invent_success(self):
        counts=Counter(); lead_review=[]
        async def complete(model,messages,max_tokens):
            counts[model]+=1
            if model=="manager-model":return {"text":'{"lead_id":"lead","brief":"Work"}' if counts[model]==1 else "QA failed; result needs checking."}
            if model=="qa-model":raise RuntimeError("QA provider unavailable")
            if counts[model]==1:return {"text":tool("team_delegate",member_id="qa",task="Test the result")}
            lead_review.append(json.dumps(messages));return {"text":"Implementation done; QA could not run."}
        runtime=self.run_for(complete)
        result=await runtime.run()
        self.assertEqual(result["run"]["status"],"completed_with_warnings")
        self.assertEqual(result["run"]["tasks"][0]["status"],"error")
        self.assertIn("QA provider unavailable","\n".join(lead_review))

    async def test_budget_reserves_manager_response_and_bounds_tools(self):
        async def complete(model,messages,max_tokens):
            if model=="manager-model":
                return {"text":'{"lead_id":"lead","brief":"Work"}' if "Select the explicit" in messages[0]["content"] else "Stopped at configured limit; unfinished work remains."}
            return {"text":tool("current_time")}
        runtime=self.run_for(complete);runtime.budget["max_calls"]=8
        result=await runtime.run()
        self.assertEqual(result["run"]["status"],"limited")
        self.assertLessEqual(result["run"]["calls"],8)
        self.assertIn("unfinished",result["text"])

    async def test_cancel_stops_children_and_pending_model(self):
        child=asyncio.Event();child_stopped=asyncio.Event();counts=Counter()
        async def complete(model,messages,max_tokens):
            counts[model]+=1
            if model=="manager-model":return {"text":'{"lead_id":"lead","brief":"Work"}'}
            if model=="qa-model":
                child.set()
                try:await asyncio.Event().wait()
                finally:child_stopped.set()
            return {"text":tool("team_delegate",member_id="qa",task="Test") if counts[model]==1 else tool("team_wait")}
        runtime=self.run_for(complete);handle=asyncio.create_task(runtime.run())
        await asyncio.wait_for(child.wait(),2);handle.cancel()
        with self.assertRaises(asyncio.CancelledError):await handle
        self.assertTrue(child_stopped.is_set())
        self.assertEqual(runtime.status,"cancelled")
        self.assertTrue(all(t.handle.done() for t in runtime.tasks.values()))

    async def test_profile_change_blocks_next_tool(self):
        changed=False
        def guard():
            if changed:raise TeamContextChanged("changed")
        async def complete(model,messages,max_tokens):
            nonlocal changed
            if model=="manager-model":return {"text":'{"lead_id":"lead","brief":"Work"}'}
            changed=True;return {"text":tool("work_read",path="secret")}
        runtime=self.run_for(complete,guard=guard)
        with self.assertRaises(TeamContextChanged):await runtime.run()
        self.assertEqual(self.broker_calls,[])

    async def test_recursive_agents_and_memory_access_are_refused(self):
        runtime=self.run_for(None);runtime.lead_id="lead"
        self.assertIn("error",await runtime._call("qa","agent_spawn",{}))
        self.assertIn("error",await runtime._call("qa","memory_list",{}))
        self.assertEqual(self.broker_calls,[])

    async def test_automatic_lead_and_images_reach_only_manager(self):
        seen=[];counts=Counter()
        async def complete(model,messages,max_tokens):
            counts[model]+=1;seen.append((model,copy.deepcopy(messages)))
            if model=="manager-model":
                return {"text":'{"lead_id":"research","brief":"Describe the attached red diagram"}' if counts[model]==1 else "Reviewed the diagram."}
            return {"text":"Diagram reviewed from the manager’s description."}
        runtime=self.run_for(complete)
        runtime.members["lead"]["is_lead"]=False
        runtime.messages=[{"role":"user","content":[{"type":"text","text":"Review diagram"},{"type":"image_url","image_url":{"url":"data:image/png;base64,TEST_IMAGE_DATA"}}]}]
        result=await runtime.run()
        self.assertEqual(result["run"]["lead_id"],"research")
        self.assertTrue(any(isinstance(m["content"],list) for m in seen[0][1]))
        for model,messages in seen[1:]:self.assertNotIn("TEST_IMAGE_DATA",json.dumps(messages))
        self.assertNotIn("TEST_IMAGE_DATA",json.dumps(result["run"]))

    async def test_manager_rejection_prevents_computer_use_broker(self):
        async def complete(model,messages,max_tokens):return {"text":'{"action":"reject","answer":"Outside user scope"}'}
        runtime=self.run_for(complete);runtime.lead_id="lead";runtime.brief="Read a document"
        result=await runtime._call("lead","team_computer",{"task":"Control desktop"})
        self.assertIn("Outside user scope",result["error"])
        self.assertEqual(self.broker_calls,[])

    async def test_model_concurrency_limit_includes_lead_and_manager(self):
        active=peak=0
        async def complete(model,messages,max_tokens):
            nonlocal active,peak
            active+=1;peak=max(peak,active)
            await asyncio.sleep(.01);active-=1
            return {"text":"Result"}
        runtime=self.run_for(complete)
        runtime.model_semaphore=asyncio.Semaphore(1)
        await asyncio.gather(*(runtime._complete(actor,[{"role":"system","content":"test"},{"role":"user","content":"task"}]) for actor in ("manager","lead","qa")))
        self.assertEqual(peak,1)
        self.assertEqual(runtime.calls,3)

    async def test_concrete_approval_passes_lead_manager_and_is_cached(self):
        seen=[]
        async def complete(model,messages,max_tokens):
            seen.append((model,json.dumps(messages)))
            return {"text":'{"action":"approve","confidence":0.99,"reason":"Explicitly requested action"}' if model=="manager-model" else '{"recommendation":"approve","reason":"Within assigned scope"}'}
        runtime=self.run_for(complete);runtime.lead_id="lead";runtime.brief="Open requested page"
        proposal={"description":"Open requested page","password":"SECRET_FIELD","code":"SECRET_CODE"}
        first=await runtime.review_approval("qa","computer","task:1",proposal)
        second=await runtime.review_approval("qa","computer","task:1",proposal)
        self.assertEqual(first["action"],"approve")
        self.assertEqual(first,second)
        self.assertEqual([row[0] for row in seen],["lead-model","manager-model"])
        self.assertNotIn("SECRET_FIELD",json.dumps(seen))
        self.assertNotIn("SECRET_CODE",json.dumps(seen))
        self.assertNotIn("PRIVATE_PERSONAL_MEMORY",seen[0][1])
        self.assertTrue(any(e["type"]=="approval" for e in self.events))

    async def test_uncertain_or_manual_approval_stays_with_user(self):
        async def complete(model,messages,max_tokens):return {"text":'{"action":"approve","confidence":0.3,"reason":"Uncertain"}'}
        runtime=self.run_for(complete);runtime.lead_id="lead"
        self.assertEqual((await runtime.review_approval("lead","dfy","question1",{"prompt":"Pay"}))["action"],"user")
        before=runtime.calls;runtime.team["manager_approvals"]=False
        self.assertEqual((await runtime.review_approval("lead","dfy","question2",{"prompt":"Pay"}))["action"],"user")
        self.assertEqual(runtime.calls,before)

    async def test_approval_budget_does_not_bypass_user(self):
        runtime=self.run_for(None);runtime.lead_id="lead";runtime.calls=30
        self.assertEqual((await runtime.review_approval("lead","computer","step1",{"description":"Click"}))["action"],"user")

class StorageAPI(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.patch=patch.object(pgdata,"MEMU_DIR",self.root);self.patch.start()
        self.active="default"
        pgdata.save_profiles({"items":[{"id":"default","name":"Default"},{"id":"other","name":"Other"}]})
        self.other_id=next(row["id"] for row in pgdata.load_profiles()["items"] if row["id"]!="default")
        self.profiles=patch.object(pgdata,"load_profiles",side_effect=lambda:{"active_id":self.active,"items":[]});self.profiles.start()
        calls=Counter()
        async def complete(model,messages,max_tokens):
            calls[model]+=1
            return {"text":'{"lead_id":"lead","brief":"Do the task"}' if model=="manager-model" and calls[model]==1 else "Verified result"}
        self.app=FastAPI();teams_api.register(self.app,complete=complete)
        self.client=TestClient(self.app)

    def tearDown(self):
        self.client.close();self.profiles.stop();self.patch.stop();self.temp.cleanup()

    def test_crud_profile_isolation_validation_and_normal_chat_run(self):
        value=definition()
        saved=self.client.post("/api/pg/teams",json={"profile_id":"default","team":value})
        self.assertEqual(saved.status_code,200)
        self.assertEqual(len(self.client.get("/api/pg/teams").json()["teams"]),1)
        self.assertTrue(all("model" not in m for p in self.client.get("/api/pg/teams").json()["presets"] for m in p["members"]))
        self.active=self.other_id
        self.assertEqual(self.client.get("/api/pg/teams?profile_id=default").status_code,409)
        self.assertEqual(self.client.get("/api/pg/teams").json()["teams"],[])
        self.active="default"

        with patch("app.teams_api.TeamRun", side_effect=lambda *args,**kwargs: TeamRun(*args,**kwargs,require_confirmation=False)):
            stream=self.client.post("/api/pg/teams/run",json={"profile_id":"default","team_id":"test-team","chat_id":"chat-test","messages":[{"role":"user","content":"Work"}],"manager_context":"PRIVATE_CONTEXT"})
        self.assertEqual(stream.status_code,200)
        events=[json.loads(line[6:]) for line in stream.text.splitlines() if line.startswith("data: ") and line!="data: [DONE]"]
        done=next(e for e in events if e["event"]=="done")
        self.assertEqual(done["run"]["status"],"completed")
        persisted=self.client.get("/api/pg/teams/runs/"+done["run"]["id"]).json()
        self.assertNotIn("PRIVATE_CONTEXT",json.dumps(persisted))
        self.assertEqual(self.client.post("/api/pg/teams/runs/"+done["run"]["id"]+"/answer",json={"profile_id":"default","request_id":"old","result":{}}).status_code,409)
        self.assertEqual(self.client.delete("/api/pg/teams/test-team?profile_id=default").status_code,200)
        self.assertEqual(self.client.get("/api/pg/teams").json()["teams"],[])

    def test_invalid_members_and_duplicate_lead(self):
        bad=definition();bad["members"][1]["is_lead"]=True
        self.assertEqual(self.client.post("/api/pg/teams",json={"team":bad}).status_code,400)
        bad=definition();bad["manager_model"]=""
        self.assertEqual(self.client.post("/api/pg/teams",json={"team":bad}).status_code,400)

class Protocols(unittest.TestCase):
    def test_existing_protocols_translate_and_partial_file_never_runs(self):
        calls,text,invalid=external_calls('```memuai-work\n{"path":"a.py"}\nprint(1)\n```\nDone')
        self.assertFalse(invalid);self.assertEqual(calls[0]["args"]["text"],"print(1)")
        self.assertIn("Done",text)
        self.assertEqual(external_calls('```memuai-work\n{"path":"a.py"}\npartial')[0],[])
        self.assertTrue(external_calls('```memuai-work\n{"path":"a.py"}\npartial')[2])

    def test_schedule_is_a_proposal_tool(self):
        calls,visible,invalid=external_calls('```schedule\n{"name":"Reminder","prompt":"Check","daily_at":"09:00"}\n```')
        self.assertFalse(invalid)
        self.assertEqual(calls[0]["tool"],"team_schedule")
        self.assertFalse(visible.strip())

if __name__=="__main__":unittest.main()
