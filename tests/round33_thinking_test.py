"""Round 33 (r33-thinking, item #3): every model offers exactly the reasoning levels it supports.

* the family table (app/reasoning_levels.py) — Gemini 3.7 Flash: Low / Medium / High only, GPT-6.1 Sol:
  no none / minimal, GLM-5.3 / Kimi K3: cannot switch off, Kimi K2.6: on / off only, Grok 4: nothing…;
* clamping of stored levels, the thinking switch, the request dialects of OpenAI-compatible APIs;
* the Proxy end to end against a recording mock: the right field reaches the API, a 400 for a reasoning value
  is retried without it and remembered, the user's override from the model card wins;
* reasoning.js (the window's pickers) clamps exactly like the Python side (same answers for a matrix);
* wiring: script tags, /pg/state carries caps.reasoning_profile, no hard-coded six-level list in the chat.

Run: python3 -m pytest -q tests/round33_thinking_test.py
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATA = tempfile.mkdtemp(prefix="r33-thinking-")
os.environ.setdefault("MEMUAI_DATA_DIR", DATA)
for _key in ("NO_PROXY", "no_proxy"):
    os.environ[_key] = "127.0.0.1,localhost"

from app import reasoning_levels as rl  # noqa: E402

def prof(model, provider="custom", raw=""):
    return rl.profile(model, provider, raw or model, use_catalog=False)

class FamilyTable(unittest.TestCase):
    def setUp(self):
        rl.reset_cache()

    def test_gemini_37_flash_low_medium_high_only(self):
        p = prof("gemini-3.7-flash")
        self.assertEqual(p["options"], ["auto", "low", "medium", "high"])
        self.assertFalse(p["can_off"])
        self.assertEqual(p["default"], "medium")
        self.assertEqual(prof("gemini-3.8-flash")["levels"], ["low", "medium", "high"])
        self.assertEqual(prof("gemini-3.6-flash")["levels"], ["minimal", "low", "medium", "high"])
        self.assertEqual(prof("gemini-3.1-pro-preview")["levels"], ["low", "medium", "high"])
        self.assertEqual(prof("gemini-3-pro-preview")["levels"], ["low", "high"])
        self.assertTrue(prof("gemini-2.5-flash")["can_off"])
        self.assertFalse(prof("gemini-2.5-pro")["can_off"])
        self.assertEqual(prof("gemini-2.0-flash")["kind"], "none")

    def test_openai(self):
        sol = prof("gpt-6.1-sol")
        self.assertNotIn("minimal", sol["levels"])
        self.assertFalse(sol["can_off"])
        self.assertEqual(sol["levels"], ["low", "medium", "high", "xhigh", "max"])
        self.assertEqual(prof("gpt-5")["levels"], ["minimal", "low", "medium", "high"])
        self.assertEqual(prof("gpt-5.1")["options"], ["auto", "off", "low", "medium", "high"])
        self.assertIn("xhigh", prof("gpt-5.2")["levels"])
        self.assertEqual(prof("o3")["levels"], ["low", "medium", "high"])
        self.assertEqual(prof("gpt-4o")["kind"], "none")
        self.assertEqual(prof("gpt-5.1-chat-latest")["kind"], "fixed")
        self.assertEqual(prof("openai/gpt-oss-120b")["levels"], ["low", "medium", "high"])

    def test_chinese_models(self):
        glm = prof("glm-5.3")
        self.assertEqual(glm["options"], ["auto", "low", "high", "max"])
        self.assertEqual(prof("glm-5-3-flash")["levels"], ["low", "high", "max"])
        self.assertEqual(prof("kimi-k3")["options"], ["auto", "low", "high", "max"])
        kimi26 = prof("kimi-k2.6")
        self.assertEqual(kimi26["kind"], "toggle")
        self.assertEqual(kimi26["options"], ["auto", "off"])
        ds = prof("deepseek-v4.1-flash")
        self.assertEqual(ds["options"], ["auto", "off", "low", "high", "max"])
        self.assertEqual(prof("deepseek-v4-1-flash")["levels"], ds["levels"])
        self.assertEqual(prof("deepseek-reasoner")["kind"], "fixed")
        qwen = prof("qwen3.8-flash")
        self.assertEqual(qwen["options"], ["auto", "off", "low", "medium", "high"])
        self.assertEqual(prof("qwen/qwen3.8-max:free")["family"], "qwen")
        self.assertEqual(prof("qwen-plus")["default"], "off")
        self.assertEqual(prof("qwen3-235b-a22b-instruct-2507")["kind"], "none")

    def test_claude_and_grok(self):
        plain = prof("claude-sonnet-4.6")
        self.assertTrue(plain["can_off"])
        self.assertEqual(plain["default"], "off")
        thinking = prof("claude-opus-4.7-thinking")
        self.assertFalse(thinking["can_off"])
        self.assertIn("xhigh", thinking["levels"])
        self.assertNotIn("xhigh", prof("claude-sonnet-4.6-thinking")["levels"])
        self.assertEqual(prof("claude-3-5-sonnet")["kind"], "none")
        self.assertEqual(prof("grok-4.7")["levels"], ["low", "medium", "high", "xhigh"])
        self.assertEqual(prof("grok-4")["kind"], "fixed")
        self.assertEqual(prof("grok-3-mini")["levels"], ["low", "high"])

    def test_web_providers_and_antigravity(self):
        self.assertEqual(rl.profile("genspark-claude-sonnet-4-6", "genspark", "claude-sonnet-4-6", use_catalog=False)["kind"], "fixed")
        ag = rl.profile("antigravity-gemini-3.7-flash", "antigravity", "gemini-3.7-flash", use_catalog=False)
        self.assertEqual(ag["options"], ["auto", "low", "medium", "high"])
        fixed = rl.profile("antigravity-gemini-3.7-flash-high", "antigravity", "gemini-3.7-flash-high", use_catalog=False)
        self.assertEqual(fixed["kind"], "fixed")
        claude = rl.profile("antigravity-claude-opus-4-6-thinking", "antigravity", "claude-opus-4-6-thinking", use_catalog=False)
        self.assertTrue(claude["can_off"])
        self.assertEqual(claude["levels"], ["low", "medium", "high", "max"])

    def test_unknown_model(self):
        self.assertEqual(rl.profile("x-mystery-1", "custom", reasoning=None, use_catalog=False)["kind"], "none")
        guessed = rl.profile("x-mystery-1", "custom", reasoning=True, use_catalog=False)
        self.assertEqual(guessed["source"], "guess")
        self.assertEqual(guessed["levels"], ["low", "medium", "high"])

class Clamp(unittest.TestCase):
    def test_clamp(self):
        g37 = prof("gemini-3.7-flash")
        self.assertEqual(rl.clamp("minimal", g37), "low")
        self.assertEqual(rl.clamp("off", g37), "low")
        self.assertEqual(rl.clamp("max", g37), "high")
        self.assertEqual(rl.clamp("xhigh", g37), "high")
        self.assertEqual(rl.clamp("auto", g37), "auto")
        self.assertEqual(rl.clamp("none", prof("gpt-5.1")), "off")
        self.assertEqual(rl.clamp("minimal", prof("gpt-5.1")), "off")
        self.assertEqual(rl.clamp("minimal", prof("gpt-6.1-sol")), "low")
        self.assertEqual(rl.clamp("medium", prof("glm-5.3")), "high")
        self.assertEqual(rl.clamp("high", prof("kimi-k2.6")), "on")
        self.assertEqual(rl.clamp("high", prof("gpt-4o")), "auto")

    def test_resolve_thinking_switch(self):
        plain = prof("claude-sonnet-4.6")
        self.assertEqual(rl.resolve(plain, True, "auto"), "medium")
        self.assertEqual(rl.resolve(plain, False, "auto"), "")
        self.assertEqual(rl.resolve(prof("qwen3.8-flash"), False, "minimal"), "off")
        self.assertEqual(rl.resolve(prof("glm-5.3"), False, "minimal"), "low")
        self.assertEqual(rl.resolve(prof("gemini-3.7-flash"), True, "auto"), "")

class Dialects(unittest.TestCase):
    def apply(self, model, level, base=""):
        body = {}
        info = rl.apply_openai_compatible(body, prof(model), level, base)
        return body, info

    def test_fields(self):
        self.assertEqual(self.apply("qwen3.8-flash", "off")[0], {"enable_thinking": False})
        self.assertEqual(self.apply("qwen3.8-flash", "low")[0], {"enable_thinking": True, "thinking_budget": 1024})
        self.assertEqual(self.apply("qwen3.8-flash", "high")[0], {"enable_thinking": True})
        self.assertEqual(self.apply("deepseek-v4.1-flash", "off")[0], {"thinking": {"type": "disabled"}})
        self.assertEqual(self.apply("deepseek-v4.1-flash", "max")[0], {"reasoning_effort": "max"})
        self.assertEqual(self.apply("glm-5.3", "low")[0], {"reasoning_effort": "low"})
        self.assertEqual(self.apply("kimi-k2.6", "off")[0], {"thinking": {"type": "disabled"}})
        self.assertEqual(self.apply("gpt-5.1", "off")[0], {"reasoning_effort": "none"})
        self.assertEqual(self.apply("gemini-2.5-flash", "off")[0], {"reasoning_effort": "none"})
        self.assertEqual(self.apply("gpt-6.1-sol", "xhigh")[0], {"reasoning_effort": "xhigh"})
        self.assertEqual(self.apply("claude-sonnet-4.6", "high")[0], {"reasoning_effort": "high"})
        self.assertEqual(self.apply("qwen/qwen3.8-max:free", "off", "https://openrouter.ai/api/v1")[0],
                         {"reasoning": {"enabled": False}})
        self.assertEqual(self.apply("claude-sonnet-4-6", "low", "https://api.anthropic.com/v1")[0],
                         {"thinking": {"type": "enabled", "budget_tokens": 2048}})
        self.assertEqual(self.apply("gemini-3.7-flash", "", "")[0], {})

    def test_local_template_kwargs(self):
        from app.providers.local import template_kwargs
        self.assertEqual(template_kwargs("off"), {"enable_thinking": False, "reasoning_effort": "low"})
        self.assertEqual(template_kwargs("on"), {"enable_thinking": True})
        self.assertEqual(template_kwargs("xhigh"), {"enable_thinking": True, "reasoning_effort": "high"})

class Persistence(unittest.TestCase):
    def setUp(self):
        rl.reset_cache()

    def test_learned_and_override(self):
        model = "Demo-glm-5.3-test"
        rl.forget_learned(model)
        rl.learn_rejection(model, "low")
        p = rl.profile(model, "custom", "glm-5.3", use_catalog=False)
        self.assertNotIn("low", p["levels"])
        self.assertEqual(p["rejected"], ["low"])
        rl.reset_cache()
        self.assertEqual(rl.learned(model)["rejected"], ["low"])
        rl.set_override(model, {"kind": "levels", "levels": ["low", "medium", "high"], "can_off": True, "default": "medium"})
        p = rl.profile(model, "custom", "glm-5.3", use_catalog=False)
        self.assertEqual(p["source"], "override")
        self.assertEqual(p["options"], ["auto", "off", "low", "medium", "high"])
        self.assertEqual(rl.learned(model), {})
        rl.set_override(model, None)
        self.assertEqual(rl.profile(model, "custom", "glm-5.3", use_catalog=False)["source"], "docs")

def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]

from fastapi import FastAPI, Request  # noqa: E402  (module level: the mock's annotations must resolve)
from fastapi.responses import JSONResponse, StreamingResponse  # noqa: E402

def make_mock():
    app = FastAPI()
    app.state.bodies = []

    @app.get("/v1/models")
    async def models():
        return {"object": "list", "data": [{"id": m, "object": "model"} for m in
                                           ("glm-5.3", "qwen3.8-flash", "gemini-3.7-flash", "gpt-4o", "kimi-k2.6")]}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        app.state.bodies.append(body)
        if body.get("model") == "glm-5.3" and (body.get("reasoning_effort") == "low"
                                               or (body.get("thinking") or {}).get("type") == "disabled"):
            return JSONResponse({"error": {"message": "Unsupported value: 'reasoning_effort'", "type": "invalid_request_error"}}, 400)
        if body.get("model") == "gemini-3.7-flash" and body.get("reasoning_effort") == "minimal":
            return JSONResponse({"error": {"message": "thinking level MINIMAL is not supported"}}, 400)

        async def gen():
            for ev in ({"choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": None}]},
                       {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}):
                yield "data: " + json.dumps(ev) + "\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")
    return app

class ProxyEndToEnd(unittest.TestCase):
    servers: list = []

    @classmethod
    def setUpClass(cls):
        import httpx
        import uvicorn
        from app.main import create_app
        cls.mock = make_mock()
        mock_port, proxy_port = free_port(), free_port()

        def run(app, port):
            server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
            threading.Thread(target=server.run, daemon=True).start()
            cls.servers.append(server)
            for _ in range(400):
                if server.started:
                    return
                time.sleep(0.05)
            raise RuntimeError("server did not start")
        run(cls.mock, mock_port)
        run(create_app(), proxy_port)
        cls.proxy = f"http://127.0.0.1:{proxy_port}"
        cls.client = httpx.Client(timeout=60, trust_env=False)
        cls.client.post(cls.proxy + "/api/admin/accounts", json={
            "provider": "custom", "label": "think",
            "fields": {"name": "Think", "base_url": f"http://127.0.0.1:{mock_port}/v1", "api_key": "sk-test",
                       "models": "glm-5.3, qwen3.8-flash, gemini-3.7-flash, gpt-4o, kimi-k2.6"}})

    @classmethod
    def tearDownClass(cls):
        for server in cls.servers:
            server.should_exit = True
        cls.client.close()

    def chat(self, model, **extra):
        self.mock.state.bodies.clear()
        body = {"model": f"Think-{model}", "messages": [{"role": "user", "content": "hi"}], "stream": True, **extra}
        resp = self.client.post(self.proxy + "/v1/chat/completions", json=body)
        self.assertEqual(resp.status_code, 200, resp.text[:300])
        self.assertIn("ok", resp.text)
        return list(self.mock.state.bodies)

    def test_dialects_reach_the_api(self):
        sent = self.chat("qwen3.8-flash", reasoning_effort="none")
        self.assertIs(sent[-1].get("enable_thinking"), False)
        sent = self.chat("gemini-3.7-flash", reasoning_effort="minimal")
        self.assertEqual(sent[-1].get("reasoning_effort"), "low")
        self.assertEqual(len(sent), 1)
        sent = self.chat("gpt-4o", reasoning_effort="high")
        self.assertNotIn("reasoning_effort", sent[-1])
        sent = self.chat("kimi-k2.6", reasoning_effort="none")
        self.assertEqual(sent[-1].get("thinking"), {"type": "disabled"})
        sent = self.chat("qwen3.8-flash")
        self.assertNotIn("enable_thinking", sent[-1])

    def test_rejected_value_is_retried_and_learned(self):
        rl.forget_learned("think-glm-5.3")
        sent = self.chat("glm-5.3", reasoning_effort="low")
        self.assertEqual(sent[-1].get("reasoning_effort"), None)
        self.assertTrue(any(b.get("reasoning_effort") == "low" for b in sent))
        rl.reset_cache()
        self.assertEqual(rl.learned("think-glm-5.3").get("rejected"), ["low"])
        sent = self.chat("glm-5.3", reasoning_effort="low")
        self.assertEqual([b.get("reasoning_effort") for b in sent], ["high"])
        state = self.client.get(self.proxy + "/api/admin/pg/state").json()
        row = next(m for m in state["models"] if m["id"] == "think-glm-5.3")
        self.assertEqual(row["caps"]["reasoning_profile"]["options"], ["auto", "high", "max"])
        forgot = self.client.post(self.proxy + "/api/admin/reasoning/forget", json={"model": "think-glm-5.3"}).json()
        self.assertEqual(forgot["effective"]["options"], ["auto", "low", "high", "max"])

    def test_override_from_the_model_card(self):
        r = self.client.post(self.proxy + "/api/admin/reasoning/override", json={
            "model": "think-gpt-4o", "override": {"kind": "levels", "levels": ["low", "high"], "default": "low"}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["effective"]["options"], ["auto", "low", "high"])
        self.assertEqual(r.json()["auto"]["kind"], "none")
        sent = self.chat("gpt-4o", reasoning_effort="medium")
        self.assertEqual(sent[-1].get("reasoning_effort"), "high")
        info = self.client.get(self.proxy + "/api/admin/reasoning/profile", params={"model": "think-gpt-4o"}).json()
        self.assertEqual(info["override"]["levels"], ["low", "high"])
        self.client.post(self.proxy + "/api/admin/reasoning/override", json={"model": "think-gpt-4o", "override": None})
        sent = self.chat("gpt-4o", reasoning_effort="medium")
        self.assertNotIn("reasoning_effort", sent[-1])

    def test_playground_chat_accepts_new_levels(self):
        self.mock.state.bodies.clear()
        resp = self.client.post(self.proxy + "/api/admin/playground/chat", json={
            "model": "Think-qwen3.8-flash", "messages": [{"role": "user", "content": "hi"}],
            "thinking": False, "effort": "off"})
        self.assertEqual(resp.status_code, 200)
        self.assertIs(self.mock.state.bodies[-1].get("enable_thinking"), False)

class JavaScriptMirror(unittest.TestCase):
    MODELS = ["gemini-3.7-flash", "gpt-6.1-sol", "gpt-5.1", "glm-5.3", "kimi-k2.6", "deepseek-v4.1-flash",
              "qwen3.8-flash", "claude-sonnet-4.6", "grok-4", "gpt-4o"]

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_clamp_matches_python(self):
        models = [{"id": m, "caps": {"reasoning_profile": rl.public(prof(m))}} for m in self.MODELS]
        levels = list(rl.CHOICES) + ["none"]
        expected = {m: {lv: rl.clamp(lv, prof(m)) for lv in levels} for m in self.MODELS}

        for m in self.MODELS:
            p = prof(m)
            for lv in levels:
                if expected[m][lv] == "on" and "on" not in p["options"]:
                    expected[m][lv] = "auto"
        script = ("global.PG_LANG='en';global.window=global;global.S={models:%s};"
                  "require(%s);const out={};for(const m of %s){out[m]={};for(const lv of %s){out[m][lv]=MemuReasoning.clamp(lv,m);}}"
                  "console.log(JSON.stringify({out, opts: Object.fromEntries(%s.map((m)=>[m, MemuReasoning.options(m)])),"
                  "label: MemuReasoning.optionLabel('auto','gemini-3.7-flash')}));") % (
            json.dumps(models), json.dumps(str(ROOT / "app" / "pgstatic" / "reasoning.js")),
            json.dumps(self.MODELS), json.dumps(levels), json.dumps(self.MODELS))
        result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["out"], expected)
        self.assertEqual(data["opts"]["gemini-3.7-flash"], ["auto", "low", "medium", "high"])
        self.assertEqual(data["opts"]["grok-4"], [])
        self.assertEqual(data["label"], "Auto · medium")

class Wiring(unittest.TestCase):
    def test_tags_and_no_fixed_list(self):
        index = (ROOT / "app" / "pgstatic" / "index.html").read_text(encoding="utf-8")
        self.assertIn("/reasoning.js?v=", index)
        self.assertLess(index.index("/reasoning.js"), index.index("/app.js"))
        self.assertIn("/reasoning_card.js", (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8"))
        app_js = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn('<select id="chat-effort" aria-label="${PG_LANG === "en" ? "Reasoning mode" : "Режим размышления"}"><option value="auto">', app_js)
        self.assertIn("MemuReasoning.optionsHtml(currentModel()", app_js)
        self.assertIn("MemuReasoningCard", (ROOT / "app" / "static" / "model_caps.js").read_text(encoding="utf-8"))
        for spec in ("MemuAIProxy.spec", "MemuAI.spec"):
            self.assertIn("app.reasoning_levels", (ROOT / spec).read_text(encoding="utf-8"))
        self.assertIn('"app.reasoning_levels"', (ROOT / "build.py").read_text(encoding="utf-8"))

    def test_preferences_keep_new_levels(self):
        from app import pgdata
        out = pgdata._normalize_preferences({"default_effort": "xhigh", "model_efforts": {"a": "off", "b": "bogus"}})
        self.assertEqual(out["default_effort"], "xhigh")
        self.assertEqual(out["model_efforts"], {"a": "off"})

if __name__ == "__main__":
    unittest.main()
