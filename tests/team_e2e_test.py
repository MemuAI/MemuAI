"""Real HTTP + SSE + tool broker + desktop UI, with an offline model transport."""
from __future__ import annotations

import asyncio
from collections import Counter
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import httpx
import uvicorn

from app import pgdata, teams_api
from tests import team_ui_test

class TeamHTTP(unittest.TestCase):
    setUpClass=classmethod(team_ui_test.TeamUI.setUpClass.__func__)
    tearDownClass=classmethod(team_ui_test.TeamUI.tearDownClass.__func__)

    def setUp(self):
        team_ui_test.TeamUI.setUp(self)
        self.temp=tempfile.TemporaryDirectory()
        self.data=patch.object(pgdata,"MEMU_DIR",Path(self.temp.name));self.data.start()
        pgdata.save_profiles({"items":[{"id":"default","name":"Test"}],"active_id":"default"})
        self.calls=Counter();self.cancelled=threading.Event();self.block=False
        async def complete(model,messages,max_tokens):
            self.calls[model]+=1
            system=messages[0]["content"]
            if "Select the explicit team lead" in system:
                roster=json.loads(messages[1]["content"].split("\n",1)[1])
                self.lead_id=roster[0]["id"]
                return {"text":json.dumps({"lead_id":self.lead_id,"brief":"Check the current time with the actual tool and report evidence."})}
            if "Present the team's result" in system:
                return {"text":"Команда проверила текущее время.","usage":{"prompt_tokens":90,"completion_tokens":15}}
            if self.block:
                try:await asyncio.sleep(100)
                finally:self.cancelled.set()
            if "Actual tool results:" not in json.dumps(messages):
                return {"text":'```memuai-tool\n[{"tool":"current_time","args":{}}]\n```'}
            return {"text":"Current time was read with current_time; the result is verified."}
        app=FastAPI();app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_methods=["*"],allow_headers=["*"])
        teams_api.register(app,complete=complete)
        self.server=uvicorn.Server(uvicorn.Config(app,host="127.0.0.1",port=0,log_level="error"))
        self.sock=socket.socket();self.sock.bind(("127.0.0.1",0));self.port=self.sock.getsockname()[1]
        self.thread=threading.Thread(target=lambda:self.server.run(sockets=[self.sock]),daemon=True);self.thread.start()
        self.base=f"http://127.0.0.1:{self.port}"
        for _ in range(100):
            if self.server.started:break
            time.sleep(.02)
        self.page.unroute("http://**/*")
        self.page.route("http://**/*",lambda route:route.continue_() if route.request.url.startswith(self.base+"/") else route.abort())
        self.page.evaluate("""base=>{
          const old=fetch;const frame=document.createElement('iframe');frame.hidden=true;document.body.append(frame);
          const network=frame.contentWindow.fetch.bind(frame.contentWindow);
          window.fetch=(url,opts)=>String(url).startsWith('/api/pg/teams')?network(base+url,opts):old(url,opts);
        }""",self.base)

    def tearDown(self):
        self.context.close();self.server.should_exit=True;self.thread.join(5);self.sock.close()
        self.data.stop();self.temp.cleanup();self.assertEqual(self.errors,[])

    def create(self):
        team_ui_test.TeamUI.create(self)

    def test_real_stream_broker_result_history_and_persisted_activity(self):
        self.create();self.page.locator("#tm-start").click()
        self.page.locator("#input").fill("Какое сейчас время? Проверь инструментом.")
        self.page.locator("#btn-send").click()
        self.page.locator("#tm-plan-start").click()
        self.page.wait_for_function("S.messages.at(-1)?.content==='Команда проверила текущее время.' && !S.messages.at(-1).streaming")
        value=self.page.evaluate("S.messages.at(-1).teamRun")
        self.assertEqual(value["status"],"completed")
        self.assertEqual(value["calls"],4)
        self.assertTrue(any(e["type"]=="tool" and e.get("status")=="done" for e in value["events"]))
        stored=httpx.get(self.base+"/api/pg/teams/runs/"+value["id"]).json()["run"]
        self.assertEqual(stored["calls"],4)
        self.assertIn("estimated_calls",stored["usage"])
        self.page.locator("[data-team-monitor]").click()
        self.page.screenshot(path=str(self.output / "team-real-stream.png"))

    def test_stop_aborts_actual_server_model_task(self):
        self.block=True;self.create();self.page.locator("#tm-start").click()
        self.page.locator("#input").fill("Долгая задача")
        self.page.locator("#btn-send").click()
        self.page.locator("#tm-plan-start").click()
        self.page.wait_for_function("S.messages.at(-1)?.teamRun?.events?.some(e=>e.type==='member' && e.member_id!=='manager' && e.status==='thinking')")
        self.page.locator("#btn-send.is-stop").click()
        self.page.wait_for_function("!S.messages.at(-1).streaming && S.messages.at(-1).teamRun.status==='cancelled'")
        self.assertTrue(self.cancelled.wait(3))
        self.assertFalse(self.page.evaluate("generationJobs.has(S.currentId)"))
        self.assertTrue(self.page.evaluate("S.messages.at(-1).teamRun.members.every(m=>!['thinking','tool','running','working'].includes(m.status))"))

    def test_plan_refinement_stays_before_work_and_is_saved(self):
        self.create();self.page.locator('#tm-start').click()
        self.page.locator('#input').fill('Проверь время');self.page.locator('#btn-send').click()
        self.page.wait_for_selector('#tm-plan-start')
        self.assertEqual(sum(self.calls.values()),1)
        self.page.locator('#tm-plan-feedback').fill('Укажи часовой пояс Москва')
        self.assertTrue(self.page.locator('#tm-plan-start').is_disabled())
        self.page.locator('#tm-plan-revise').click()
        self.page.wait_for_selector('#tm-plan-start:not([disabled])')
        self.assertEqual(sum(self.calls.values()),2)
        self.assertEqual(self.page.evaluate('S.messages.at(-1).teamClarifications'),['Укажи часовой пояс Москва'])
        self.page.locator('#tm-plan-start').click()
        self.page.wait_for_function("S.messages.at(-1)?.content==='Команда проверила текущее время.' && !S.messages.at(-1).streaming")
        self.assertTrue(self.page.evaluate("S.messages.at(-1).teamRun.events.some(e=>e.type==='user_clarification')"))

if __name__=="__main__":unittest.main()
