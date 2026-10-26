"""Round 9: Design 3, the «+» menu, Deep Research, real-time Live models, prompts, Setup.

Everything here is offline: the research engine gets fake search, page and
model functions; the Live adapters are checked on recorded event shapes of
each protocol; routes run through TestClient with a temporary data folder.
"""
from __future__ import annotations

import asyncio
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import deep_research, pgdata, realtime_live, themes

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "pgstatic"

class FakeWeb:
    """Search, page and model stand-ins that record what the engine asked for."""

    def __init__(self, planner_queries=None, follow_ups=None, done_after=1):
        self.planner_queries = planner_queries or [
            ["solar panel efficiency 2026", "perovskite record efficiency"],
            ["solar panel cost per watt", "solar module price trend"],
        ]
        self.follow_ups = follow_ups or []
        self.done_after = done_after
        self.reviews = 0
        self.searches: list[str] = []
        self.fetched: list[str] = []
        self.calls: list[str] = []

    async def complete(self, messages):
        system = messages[0]["content"]
        user = messages[1]["content"]
        if system.startswith("PLANNER"):
            self.calls.append("plan")
            return "Here is the plan:\n```json\n" + json.dumps({
                "title": "Solar panels", "objective": "Efficiency and cost", "language": "en",
                "complexity": "standard", "reasoning": "Two angles, a handful of searches.",
                "sub_questions": [{"id": f"q{i + 1}", "question": f"Question {i + 1}", "queries": queries}
                                  for i, queries in enumerate(self.planner_queries)],
                "report_outline": ["Efficiency", "Cost"],
            }) + "\n```"
        if system.startswith("Select the web results"):
            self.calls.append("select")
            indices = [int(n) for n in re.findall(r"^(\d+)\. ", user, re.M)]
            return json.dumps({"read": indices[:4], "reason": "best"})
        if system.startswith("READER"):
            self.calls.append("read")
            numbers = [int(n) for n in re.findall(r'<source n="(\d+)"', user)]
            return json.dumps({"sources": [{"n": n, "relevance": 2, "date": "2026-05-01", "quality": "reputable",
                                            "sub_questions": ["q1"],
                                            "findings": [f"Finding {n}: efficiency reached 27.1% in 2026."]}
                                           for n in numbers]})
        if system.startswith("REVIEWER"):
            self.calls.append("review")
            self.reviews += 1
            done = self.reviews >= self.done_after
            return json.dumps({"coverage": [{"id": "q1", "status": "answered", "note": "ok"}],
                               "contradictions": ["[1] says 27.1%, [2] says 26.8%"], "done": done,
                               "reason": "enough" if done else "gaps",
                               "follow_up_queries": [] if done else [{"query": q, "sub": "q2"} for q in self.follow_ups]})
        raise AssertionError("unexpected model call: " + system[:40])

    async def search(self, query, limit):
        self.searches.append(query)
        slug = re.sub(r"\W+", "-", query.lower())
        return [
            {"title": f"{query} A", "url": f"https://example.com/{slug}?utm_source=x", "snippet": "a"},
            {"title": f"{query} A dup", "url": f"https://www.example.com/{slug}/", "snippet": "dup"},
            {"title": f"{query} B", "url": f"https://news-{slug[:12]}.org/story", "snippet": "b"},
            {"title": "pin", "url": f"https://www.pinterest.com/pin/{slug}", "snippet": "p"},
        ]

    async def fetch(self, url, limit):
        self.fetched.append(url)
        if "news-" in url and "cost" in url:
            raise ValueError("blocked by bot protection")
        return {"url": url, "title": "Page " + url, "text": ("Solar panel data. " * 40)[:limit]}

def run_engine(web: FakeWeb, **limits):
    events: list[dict] = []
    engine = deep_research.ResearchEngine(
        complete=web.complete, search=web.search, fetch=web.fetch, emit=events.append,
        limits=deep_research.ResearchLimits(**limits),
        prompts={"planner": "PLANNER", "reader": "READER", "reviewer": "REVIEWER"}, today="2026-09-26")
    result = asyncio.run(engine.run("How efficient and how cheap are solar panels now?", "User: hi"))
    return engine, result, events

class DeepResearchEngineTest(unittest.TestCase):
    def test_full_run_produces_a_numbered_dossier(self) -> None:
        web = FakeWeb()
        engine, result, events = run_engine(web, max_queries=20, max_rounds=3, max_sources=12)
        kinds = {event["type"] for event in events}
        self.assertTrue({"plan", "phase", "query", "source", "notes", "review", "stats"} <= kinds)
        numbers = [row["n"] for row in result["sources"]]
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)), "citations are renumbered 1..N")
        self.assertIn("[1]", result["dossier"])
        self.assertIn("Finding", result["dossier"])
        self.assertIn(result["sources"][0]["url"], result["dossier"])
        self.assertEqual(result["plan"]["title"], "Solar panels")
        self.assertEqual(result["stats"]["rounds"], 1, "the reviewer said the evidence was enough")

        self.assertEqual(len([u for u in engine.sources]), len(set(engine.sources)))
        self.assertFalse(any("pinterest" in url for url in web.fetched), "login-walled hosts are skipped")
        self.assertIn("contradictions", result["review"])

    def test_the_model_decides_but_the_budget_is_a_hard_ceiling(self) -> None:
        many = [[f"query {i}-{j}" for j in range(8)] for i in range(4)]
        web = FakeWeb(planner_queries=many, follow_ups=[f"follow {k}" for k in range(20)], done_after=5)
        engine, result, _events = run_engine(web, max_queries=10, max_rounds=3, max_sources=30)
        self.assertLessEqual(len(web.searches), 10)
        self.assertEqual(result["plan"]["planned_queries"], 7, "70% of the budget in the first round")
        self.assertEqual(len(set(q.lower() for q in web.searches)), len(web.searches), "no repeated queries")
        self.assertLessEqual(result["stats"]["rounds"], 3)

    def test_planner_failure_falls_back_to_the_topic(self) -> None:
        web = FakeWeb()

        async def broken_plan(messages):
            if messages[0]["content"].startswith("PLANNER"):
                return "Sorry, I cannot produce JSON."
            return await FakeWeb.complete(web, messages)

        web.complete = broken_plan  # type: ignore[assignment]
        _engine, result, _events = run_engine(web, max_queries=6, max_rounds=1, max_sources=4)
        self.assertTrue(result["sources"])
        self.assertTrue(any("solar" in q.lower() for q in web.searches))

    def test_no_evidence_is_an_honest_error(self) -> None:
        web = FakeWeb()

        async def empty(query, limit):
            return []

        web.search = empty  # type: ignore[assignment]
        with self.assertRaisesRegex(RuntimeError, "источник"):
            run_engine(web, max_queries=6, max_rounds=1, max_sources=4)

    def test_json_extraction_and_url_normalisation(self) -> None:
        self.assertEqual(deep_research.extract_json('prose {not json} then {"a": 1,}')["a"], 1)
        self.assertEqual(deep_research.extract_json("```json\n{\"b\": [1, 2]}\n```")["b"], [1, 2])
        self.assertEqual(deep_research.extract_json("nothing here"), {})
        self.assertEqual(deep_research.normalize_url("https://www.Example.com/a/?utm_source=x&id=2#top"),
                         "https://example.com/a?id=2")
        limits = deep_research.ResearchLimits.from_preferences(
            {"research_max_queries": 9999, "research_max_rounds": "x", "research_max_sources": 1})
        self.assertEqual((limits.max_queries, limits.max_rounds, limits.max_sources), (150, 4, 4))

def live_config(provider: str, **extra) -> realtime_live.LiveConfig:
    prefs = {"voice_realtime_provider": provider,
             "voice_realtime_api_keys": {provider: "sk-secret-123"}, **extra}
    return realtime_live.LiveConfig.from_preferences(prefs, "Be brief.")

class RealtimeAdapterTest(unittest.TestCase):
    def test_configuration_is_validated_before_any_connection(self) -> None:
        with self.assertRaisesRegex(realtime_live.RealtimeConfigError, "API-ключ"):
            realtime_live.LiveConfig.from_preferences({"voice_realtime_provider": "gemini"})
        with self.assertRaisesRegex(realtime_live.RealtimeConfigError, "WebSocket"):
            realtime_live.LiveConfig.from_preferences({"voice_realtime_provider": "custom"})
        with self.assertRaisesRegex(realtime_live.RealtimeConfigError, "ws://"):
            realtime_live.LiveConfig.from_preferences({"voice_realtime_provider": "custom",
                                                       "voice_realtime_base_url": "ws://example.com/rt"})
        local = realtime_live.LiveConfig.from_preferences({"voice_realtime_provider": "custom",
                                                           "voice_realtime_base_url": "ws://127.0.0.1:9000/v1/realtime",
                                                           "voice_realtime_model": "local-voice"})
        self.assertEqual(local.api_key, "")

    def test_gpt_live_session_and_client_delegation(self) -> None:
        adapter = realtime_live.build_adapter(live_config("openai-live"))
        self.assertIsInstance(adapter, realtime_live.GptLiveAdapter)
        self.assertEqual(adapter.urls(), ["wss://api.openai.com/v1/live/sessions"])
        self.assertEqual(adapter.headers()["Authorization"], "Bearer sk-secret-123")
        start = adapter.setup_messages()[0]
        self.assertEqual(start["type"], "session.start")
        self.assertEqual(start["session"]["model"], "gpt-live-1")
        self.assertIsNone(start["session"]["delegation"], "client mode: MemuAI answers delegated questions")
        self.assertEqual(start["session"]["audio"]["format"], {"type": "audio/pcm", "rate": 24000})
        self.assertEqual(adapter.translate({"type": "session.started", "session": {}})[0]["type"], "ready")
        self.assertEqual(adapter.audio_message("QUJD"), {"type": "session.input_audio.append", "audio": "QUJD"})
        self.assertEqual(adapter.translate({"type": "session.output_audio.delta", "delta": "AAA="}),
                         [{"type": "audio", "data": "AAA="}])
        self.assertEqual(adapter.translate({"type": "session.input_transcript.delta", "delta": "What is"}),
                         [{"type": "user_transcript", "delta": "What is"}])
        adapter.translate({"type": "session.output_transcript.delta", "delta": "Let me check"})
        self.assertIn("User: What is", adapter.transcript_text())
        self.assertEqual(adapter.translate({"type": "session.delegation.created",
                                            "delegation": {"id": "item_1", "target": "client"}}),
                         [{"type": "delegate", "id": "item_1"}])
        reply = adapter.delegation_reply("item_1", "Paris.")
        self.assertEqual((reply["type"], reply["delegation_id"]), ("session.thinking.append", "item_1"))
        responses = realtime_live.build_adapter(live_config("openai-live", voice_realtime_delegation="responses",
                                                            voice_realtime_backend_model="gpt-5.5"))
        self.assertEqual(responses.setup_messages()[0]["session"]["delegation"]["responses"]["model"], "gpt-5.5")

    def test_openai_realtime_dialects_and_event_variants(self) -> None:
        adapter = realtime_live.build_adapter(live_config("openai-realtime"))
        self.assertIn("model=gpt-realtime", adapter.urls()[0])
        session = adapter.setup_messages()[0]["session"]
        self.assertEqual(session["type"], "realtime")
        self.assertEqual(session["audio"]["output"]["voice"], "marin")
        self.assertEqual(session["audio"]["input"]["turn_detection"]["type"], "semantic_vad")
        self.assertEqual(adapter.translate({"type": "session.created"})[0]["type"], "ready")
        self.assertEqual(adapter.translate({"type": "response.output_audio.delta", "delta": "AA"})[0]["type"], "audio")
        self.assertEqual(adapter.translate({"type": "response.audio.delta", "delta": "BB"})[0]["data"], "BB")
        self.assertEqual(adapter.translate({"type": "response.output_audio_transcript.delta", "delta": "Hi"}),
                         [{"type": "assistant_transcript", "delta": "Hi"}])
        self.assertEqual(adapter.translate({"type": "input_audio_buffer.speech_started"}), [{"type": "interrupted"}, {"type": "speech_started"}])
        done = adapter.translate({"type": "conversation.item.input_audio_transcription.completed", "transcript": "Stop"})
        self.assertEqual(done, [{"type": "user_transcript", "text": "Stop", "final": True}])
        self.assertEqual(adapter.translate({"type": "response.done", "response": {"status": "completed"}}),
                         [{"type": "turn_complete"}])
        self.assertEqual(adapter.translate({"type": "error", "error": {"code": "response_cancel_not_active",
                                                                        "message": "no active response"}}), [])
        beta = realtime_live.build_adapter(realtime_live.LiveConfig.from_preferences({
            "voice_realtime_provider": "custom", "voice_realtime_base_url": "wss://rt.example.com/v1/realtime",
            "voice_realtime_model": "qwen-omni", "voice_realtime_dialect": "beta"}))
        self.assertEqual(beta.headers().get("OpenAI-Beta"), "realtime=v1")
        self.assertEqual(beta.setup_messages()[0]["session"]["input_audio_format"], "pcm16")

    def test_xai_uses_session_level_voice_and_its_transcript_events(self) -> None:
        adapter = realtime_live.build_adapter(live_config("xai"))
        self.assertTrue(adapter.urls()[0].startswith("wss://api.x.ai/v1/realtime?model=grok-voice-latest"))
        session = adapter.setup_messages()[0]["session"]
        self.assertEqual(session["voice"], "eve")
        self.assertNotIn("type", session)
        self.assertEqual(adapter.translate({"type": "response.output_transcript.delta", "delta": "Hey"}),
                         [{"type": "assistant_transcript", "delta": "Hey"}])
        updated = adapter.translate({"type": "input_audio_transcription.updated", "transcript": "hello there"})
        self.assertEqual(updated[0]["text"], "hello there")

    def test_gemini_live_setup_audio_and_server_content(self) -> None:
        adapter = realtime_live.build_adapter(live_config("gemini"))
        urls = adapter.urls()
        self.assertIn("v1beta", urls[0])
        self.assertIn("v1alpha", urls[1], "a model missing from v1beta is retried on v1alpha")
        self.assertTrue(all("key=sk-secret-123" in url for url in urls))
        self.assertEqual(adapter.headers(), {})
        setup = adapter.setup_messages()[0]["setup"]
        self.assertEqual(setup["model"], "models/gemini-3.8-live")
        self.assertEqual(setup["generationConfig"]["responseModalities"], ["AUDIO"])
        self.assertTrue(setup["systemInstruction"]["parts"][0]["text"].startswith("Be brief."))
        self.assertEqual(adapter.audio_message("QQ")["realtimeInput"]["audio"]["mimeType"], "audio/pcm;rate=16000")
        self.assertEqual(adapter.translate({"setupComplete": {}})[0]["type"], "ready")
        events = adapter.translate({"serverContent": {
            "inputTranscription": {"text": "Weather?"},
            "modelTurn": {"parts": [{"inlineData": {"mimeType": "audio/pcm;rate=24000", "data": "UUU="}}]},
            "outputTranscription": {"text": "Sunny."}, "turnComplete": True}})
        self.assertEqual([e["type"] for e in events], ["user_transcript", "audio", "assistant_transcript", "turn_complete"])
        self.assertEqual(adapter.translate({"serverContent": {"interrupted": True}}), [{"type": "interrupted"}])
        self.assertEqual(realtime_live.decode_frame(b'{"setupComplete": {}}'), {"setupComplete": {}})
        self.assertNotIn("sk-secret-123", realtime_live.redact("failed wss://x?key=sk-secret-123", adapter.config))

class Round9PreferencesTest(unittest.TestCase):
    def test_new_preferences_are_normalised(self) -> None:
        prefs = pgdata._normalize_preferences({
            "voice_realtime_enabled": "true", "voice_realtime_provider": "made-up",
            "voice_realtime_api_keys": {"gemini": " key ", "evil": "x", "xai": ""},
            "voice_realtime_turn_detection": "loud", "voice_realtime_dialect": "v9",
            "voice_realtime_delegation": "everyone", "research_max_queries": 5000,
            "research_max_rounds": 0, "research_max_sources": "many",
            "composer_pinned_tools": ["research", "../x", "visualize"],
        })
        self.assertTrue(prefs["voice_realtime_enabled"])
        self.assertEqual(prefs["voice_realtime_provider"], "openai-live")
        self.assertEqual(prefs["voice_realtime_api_keys"], {"gemini": "key"})
        self.assertEqual((prefs["voice_realtime_turn_detection"], prefs["voice_realtime_dialect"],
                          prefs["voice_realtime_delegation"]), ("auto", "ga", "memuai"))
        self.assertEqual((prefs["research_max_queries"], prefs["research_max_rounds"], prefs["research_max_sources"]),
                         (150, 1, 30))
        self.assertEqual(prefs["composer_pinned_tools"], ["research", "visualize"])

    def test_prompts_cover_everyday_answers_and_new_contracts(self) -> None:
        for section in ("<understanding_the_request>", "<answer_shape>", "<explaining>", "<formatting>",
                        "<accuracy_and_honesty>", "<work_quality>", "<conversation_style>",
                        "<local_tools>", "<internal_protocols>", "[MEM:", "```schedule"):
            self.assertIn(section, pgdata.DEFAULT_PROMPT, section)
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn("<answer_shape>", js, "the app.js fallback mirrors DEFAULT_PROMPT")
        ids = {row["id"] for row in pgdata.INTERNAL_PROMPTS}
        for prompt_id in ("deep-research-planner", "deep-research-reader", "deep-research-reviewer",
                          "deep-research-report", "voice-realtime", "voice-realtime-delegate", "visualization-mode"):
            self.assertIn(prompt_id, ids)
        report = pgdata.internal_prompt("deep-research-report")
        self.assertIn("[3]", report)
        self.assertIn("Never invent sources", report)

class Round9RouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self._patch = patch.object(pgdata, "MEMU_DIR", Path(self._temp.name))
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self._temp.cleanup()

    def client(self) -> TestClient:
        from app.playground_api import create_app
        return TestClient(create_app(), base_url="http://127.0.0.1")

    def test_research_route_streams_progress_and_the_dossier(self) -> None:
        class FakeEngine:
            def __init__(self, *, emit, **_kwargs):
                self.emit = emit

            async def run(self, query, context):
                await self.emit({"type": "plan", "plan": {"title": query}})
                await self.emit({"type": "phase", "phase": "searching", "round": 1})
                return {"plan": {"title": query}, "dossier": "Evidence [1]",
                        "sources": [{"n": 1, "title": "A", "url": "https://a.example", "domain": "a.example"}],
                        "stats": {"queries": 3}, "review": {}, "queries": []}

            def stats(self):
                return {}

        with self.client() as client, patch.object(deep_research, "ResearchEngine", FakeEngine):
            self.assertEqual(client.post("/api/pg/research", json={"query": ""}).status_code, 400)
            response = client.post("/api/pg/research", json={"query": "Topic", "model": "demo"})
            self.assertEqual(response.status_code, 200)
            events = [json.loads(line[6:]) for line in response.text.splitlines()
                      if line.startswith("data: ") and line != "data: [DONE]"]
            self.assertEqual([e["type"] for e in events], ["plan", "phase", "done"])
            self.assertEqual(events[-1]["dossier"], "Evidence [1]")
            self.assertEqual(events[-1]["model"], "demo")
            self.assertTrue(response.text.rstrip().endswith("data: [DONE]"))

    def test_live_relay_rejects_foreign_pages_and_explains_missing_setup(self) -> None:

        url = "ws://127.0.0.1/api/voice/realtime"
        with self.client() as client:
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect(url, headers={"origin": "https://evil.example"}) as ws:
                    ws.receive_text()
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect("ws://attacker.example/api/voice/realtime") as ws:
                    ws.receive_text()
            with client.websocket_connect(url, headers={"origin": "http://127.0.0.1"}) as ws:
                ws.send_text(json.dumps({"type": "start", "instructions": "hi"}))
                error = json.loads(ws.receive_text())
                self.assertEqual((error["type"], error["code"]), ("error", "config"))
                self.assertIn("API-ключ", error["message"])
                self.assertEqual(json.loads(ws.receive_text())["type"], "closed")
            catalog = client.get("/api/voice/realtime/catalog").json()
            self.assertIn("gemini", [row["id"] for row in catalog["providers"]])
            self.assertIn("openai-live", [row["id"] for row in catalog["providers"]])
            self.assertNotIn("url", catalog["providers"][0], "the catalogue carries no protocol internals")
            self.assertEqual(client.post("/api/voice/realtime/test").status_code, 400)

    def test_live_relay_end_to_end_with_a_fake_realtime_provider(self) -> None:
        """A local WebSocket server speaks the OpenAI Realtime protocol."""
        import socket
        import threading
        from websockets.asyncio.server import serve

        seen: list[dict] = []
        ready = threading.Event()
        loop = asyncio.new_event_loop()

        async def provider(connection):
            seen.append({"auth": connection.request.headers.get("Authorization", "")})
            async for raw in connection:
                event = json.loads(raw)
                seen.append(event)
                if event["type"] == "session.update":
                    await connection.send(json.dumps({"type": "session.created", "session": {}}))
                elif event["type"] == "input_audio_buffer.append":
                    await connection.send(json.dumps({"type": "input_audio_buffer.speech_started"}))
                    await connection.send(json.dumps({"type": "conversation.item.input_audio_transcription.completed",
                                                      "transcript": "Привет"}))
                    await connection.send(json.dumps({"type": "response.output_audio.delta", "delta": "AAAA"}))
                    await connection.send(json.dumps({"type": "response.output_audio_transcript.delta", "delta": "Здравствуйте"}))
                    await connection.send(json.dumps({"type": "response.done", "response": {"status": "completed"}}))
                    await connection.close()

        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()

        stop: dict = {}

        async def main():
            stop["future"] = asyncio.get_running_loop().create_future()
            async with serve(provider, "127.0.0.1", port):
                ready.set()
                await stop["future"]

        thread = threading.Thread(target=lambda: loop.run_until_complete(main()), daemon=True)
        thread.start()
        self.assertTrue(ready.wait(10))
        try:
            pgdata.save_preferences({"voice_realtime_enabled": True, "voice_realtime_provider": "custom",
                                     "voice_realtime_base_url": f"ws://127.0.0.1:{port}/v1/realtime",
                                     "voice_realtime_model": "fake-live", "voice_realtime_voice": "nova",
                                     "voice_realtime_api_keys": {"custom": "local-key"}})
            with self.client() as client, \
                    client.websocket_connect("ws://127.0.0.1/api/voice/realtime",
                                             headers={"origin": "http://127.0.0.1"}) as ws:
                ws.send_text(json.dumps({"type": "start", "instructions": "Говори кратко."}))
                first = json.loads(ws.receive_text())
                self.assertEqual((first["type"], first["model"], first["input_rate"]), ("ready", "fake-live", 24000))
                ws.send_text(json.dumps({"type": "audio", "data": "UENNMTY="}))
                received = []
                while True:
                    event = json.loads(ws.receive_text())
                    received.append(event)
                    if event["type"] == "closed":
                        break
                kinds = [event["type"] for event in received]
                self.assertIn("audio", kinds)
                self.assertIn({"type": "user_transcript", "text": "Привет", "final": True}, received)
                self.assertIn({"type": "assistant_transcript", "delta": "Здравствуйте"}, received)
                self.assertIn("turn_complete", kinds)
            self.assertEqual(seen[0]["auth"], "Bearer local-key", "the key stays on the server side")
            update = next(e for e in seen if e.get("type") == "session.update")
            self.assertTrue(update["session"]["instructions"].startswith("Говори кратко."))
            self.assertEqual(update["session"]["audio"]["output"]["voice"], "nova")
            self.assertIn({"type": "input_audio_buffer.append", "audio": "UENNMTY="}, seen)
        finally:
            if "future" in stop:
                loop.call_soon_threadsafe(stop["future"].set_result, None)
            thread.join(timeout=10)
            loop.close()

class Round9InterfaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.css = (STATIC / "design.css").read_text(encoding="utf-8")
        cls.tools = (STATIC / "tools.js").read_text(encoding="utf-8")
        cls.research = (STATIC / "research.js").read_text(encoding="utf-8")
        cls.realtime = (STATIC / "realtime.js").read_text(encoding="utf-8")

    def test_design_layer_loads_last_and_parses(self) -> None:
        self.assertLess(self.html.index("/appearance.css?"), self.html.index("/design.css?"))
        self.assertLess(self.html.index("/docs.css?"), self.html.index("/design.css?"))

        self.assertEqual(self.css.count("/*"), self.css.count("*/"))
        for token in ("--chrome:", "--canvas:", "--canvas-line:", "--brand:"):
            self.assertIn(token, self.css)
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{6}\b", self.css), [], "colours come from themes.py tokens")

    def test_plus_menu_replaces_the_paper_clip(self) -> None:
        self.assertIn('id="btn-plus"', self.html)
        self.assertNotIn("btn-attach", self.html + self.js)
        self.assertIn('id="tool-chips"', self.html)
        composer = self.html[self.html.index('<div class="composer">'):self.html.index('id="module-slot-composer"')]
        self.assertIn('id="cap-toggles"', composer, "tool toggles live in the composer")
        self.assertIn("MemuTools.open(event.currentTarget)", self.js)
        self.assertIn("MemuTools.renderChips()", self.js)
        for tool in ("files", "paste", "screenshot", "image", "search", "research",
                     "live", "dictate", "skills", "instructions", "schedule", "temp"):
            self.assertIn(f'id: "{tool}"', self.tools, tool)

        for tool in ("think", "visualize"):
            self.assertNotIn(f'id: "{tool}"', self.tools, tool)

        agents_row = self.tools[self.tools.index('id: "agents"'):]
        self.assertIn("hidden: !project", agents_row[:500])
        for script in ("tools.js", "research.js", "agents.js", "realtime.js"):
            self.assertIn(f"/{script}?", self.html)

    def test_research_and_visualization_are_wired_into_generation(self) -> None:
        generate = self.js[self.js.index("async function generate("):self.js.index("const PROVIDER_IMAGE_HOSTS")]
        self.assertIn("MemuResearch.run(assistant", generate)
        self.assertIn("researchDossiers.set(assistant", generate)
        self.assertIn("<research_dossier>", generate)
        self.assertIn("<deep_research_report>", generate)
        self.assertIn("web_search: searchEnabled && searchRequested && !usedResearch", generate)
        self.assertIn('internalPromptText("visualization-mode")', generate)

        self.assertNotIn("agents/run", generate)
        self.assertIn("MemuAgents.allowed(sourceMessages, sourceCaps, sourceMode)", generate)
        self.assertIn("readSSE(response.body", self.research)
        self.assertIn('fetch("/api/pg/research"', self.research)
        self.assertIn("MemuResearch.stepHtml(m, i)", self.js)
        self.assertIn('case "/research":', self.js)
        self.assertIn("linkCitations", self.js)

    def test_live_supports_both_classic_and_real_time_modes(self) -> None:
        self.assertIn("function liveRealtimeEnabled()", self.js)
        self.assertIn("if (realtime) startRealtimeLive();", self.js)
        self.assertIn("MemuRealtime.start({", self.js)
        self.assertIn('"/realtime-worklet.js', self.realtime)
        self.assertIn('"/api/voice/realtime"', self.realtime)
        self.assertIn("stopPlayback()", self.realtime)
        self.assertTrue((STATIC / "realtime-worklet.js").is_file())
        for element in ("live-orb-canvas", "live-mute", "live-captions", "live-text-form"):
            self.assertIn(f'id="{element}"', self.html)

    def test_brand_partner_hue_exists_for_every_accent(self) -> None:
        css = themes.theme_css()
        for key, row in themes.ACCENTS.items():
            self.assertIn("light2", row, key)
            self.assertIn(row["dark2"], css)
        self.assertIn("--accent-2: var(--accent2-dark)", css)

class GensparkSnapshotDeltaTest(unittest.TestCase):
    """The smoke gate found a repeated answer: a cumulative snapshot sent as a delta."""

    def run_parser(self, deltas):
        from app.providers.gs.stream import iter_upstream_events
        from tests.protocol_contract_test import FakeLineResponse
        lines = [("data: " + json.dumps({"type": "message_field_delta", "field_name": "content",
                                           "delta": delta}, ensure_ascii=False)).encode() for delta in deltas]

        async def collect():
            return [e.text async for e in iter_upstream_events(FakeLineResponse(lines)) if e.kind == "text"]

        return "".join(asyncio.run(collect()))

    def test_full_repeat_is_dropped_but_markdown_repeats_survive(self) -> None:
        text = "Привет! Чем я могу помочь вам сегодня?"
        self.assertEqual(self.run_parser(["Привет! ", "Чем я могу помочь вам сегодня?", text]), text)
        self.assertEqual(self.run_parser(["- a\n", "- ", "b\n", "**x**", " and ", "**", "y", "**"]),
                         "- a\n- b\n**x** and **y**")

class Round9InstallerTest(unittest.TestCase):
    """Round 9 redesigned the Inno wizard (artwork, steps, no "folder exists" prompt,
    one surface colour). Round 20 replaced Inno with the custom installer in
    ``installer/``; the same promises are checked on it."""

    UI = ROOT / "installer" / "ui"

    def test_redesigned_wizard_ships_its_artwork_and_skips_the_folder_prompt(self) -> None:
        from installer import engine, products

        for name in ("assets/logo.png", "assets/logo-proxy.png", "fonts/inter-latin-wght.woff2",
                     "fonts/inter-cyrillic-wght.woff2", "fonts/Inter-OFL.txt", "scene.js", "style.css"):
            self.assertTrue((self.UI / name).is_file(), name)
        spec = (ROOT / "installer" / "setup.spec").read_text(encoding="utf-8")
        self.assertIn('UI = ROOT / "installer" / "ui"', spec)
        self.assertIn('path.name != "mock.js"', spec)
        html = (self.UI / "index.html").read_text(encoding="utf-8")
        self.assertIn('src="assets/logo.png"', html)
        self.assertNotIn("http://", html.replace("http://www.w3.org", ""))
        self.assertNotIn("https://", html, "the interface works offline")

        self.assertEqual(html.count('class="step" data-step='), 5)
        i18n = (self.UI / "i18n.js").read_text(encoding="utf-8")
        for key in ("step.edition", "step.location", "step.options", "step.review", "step.install"):
            self.assertEqual(i18n.count(f'"{key}":'), 2, key)

        class Quiet(engine.Platform):
            def __init__(self, base: Path) -> None:
                self.base = base

            def env(self, name: str) -> str:
                return str(self.base / name) if name in ("LOCALAPPDATA", "APPDATA", "USERPROFILE", "TEMP") else ""

            def disk_free(self, path) -> int:
                return 10 ** 12

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            eng = engine.Engine(products.SUITE, Quiet(base))
            own = base / "Apps" / "MemuAI"
            (own / "MemuAIProxy").mkdir(parents=True)
            (own / "install.json").write_text("{}", encoding="utf-8")
            result = eng.check_dir(str(own), ["proxy"])
            self.assertTrue(result["ok"])
            self.assertFalse(result["adjusted"])
            foreign = base / "Docs"
            foreign.mkdir()
            (foreign / "thesis.docx").write_text("x", encoding="utf-8")
            result = eng.check_dir(str(foreign), ["proxy"])
            self.assertTrue(result["ok"] and result["adjusted"])
            self.assertEqual(Path(result["path"]), foreign / "MemuAI")

    def test_window_and_page_share_one_surface_colour(self) -> None:

        from installer import main as setup_main
        import inspect

        css = (self.UI / "style.css").read_text(encoding="utf-8")
        bg = re.search(r"--bg:\s*(#[0-9a-fA-F]{6})", css).group(1)
        self.assertIn(f'background_color="{bg.upper()}"', inspect.getsource(setup_main.run_window))
        self.assertIn("html, body {", css)
        self.assertIn("background: var(--bg)", css)

if __name__ == "__main__":
    unittest.main()
