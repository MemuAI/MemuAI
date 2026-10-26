"""Round 13: MemuAI Live sees the camera and the screen and can search the web.

The provider is a local WebSocket server that speaks the Gemini Live protocol,
including the way a free-tier key refuses Google Search.
"""
from __future__ import annotations

import os
import tempfile

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

import asyncio  # noqa: E402
import json  # noqa: E402
import socket  # noqa: E402
import threading  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import patch  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app import pgdata, realtime_live, websearch  # noqa: E402

STATIC = Path(__file__).resolve().parents[1] / "app" / "pgstatic"

def config(provider: str, **extra) -> realtime_live.LiveConfig:
    prefs = {"voice_realtime_provider": provider, "voice_realtime_api_keys": {provider: "sk-secret"}, **extra}
    return realtime_live.LiveConfig.from_preferences(prefs, "Be brief.")

class LiveAdaptersSeeAndSearchTest(unittest.TestCase):
    def test_gemini_streams_frames_and_steps_down_from_google_search(self) -> None:
        adapter = realtime_live.build_adapter(config("gemini"))
        self.assertEqual((adapter.video_mode, adapter.search_mode), ("stream", "native"))
        setup = adapter.setup_messages()[0]["setup"]
        self.assertEqual(setup["tools"], [{"googleSearch": {}}])
        self.assertIn("look things up on the web", setup["systemInstruction"]["parts"][0]["text"])
        self.assertIn("live video", setup["systemInstruction"]["parts"][0]["text"])
        frame = adapter.video_messages("SlBFRw==", "image/jpeg")
        self.assertEqual(frame, [{"realtimeInput": {"video": {"data": "SlBFRw==", "mimeType": "image/jpeg"}}}])
        self.assertEqual(adapter.video_messages("x", "text/html")[0]["realtimeInput"]["video"]["mimeType"], "image/jpeg")

        self.assertTrue(adapter.degrade())
        self.assertEqual(adapter.search_mode, "tool")
        names = [row["name"] for row in adapter.setup_messages()[0]["setup"]["tools"][0]["functionDeclarations"]]
        self.assertEqual(names, ["web_search", "open_page"])
        self.assertTrue(adapter.degrade())
        self.assertNotIn("tools", adapter.setup_messages()[0]["setup"])
        self.assertNotIn("look things up", adapter.setup_messages()[0]["setup"]["systemInstruction"]["parts"][0]["text"])
        self.assertFalse(adapter.degrade(), "nothing is left to drop")

    def test_gemini_tool_calls_and_grounding_sources(self) -> None:
        adapter = realtime_live.build_adapter(config("gemini"))
        self.assertEqual(adapter.ready_event()["video"], "stream")
        self.assertEqual(adapter.ready_event()["search"], "native")
        call = adapter.translate({"toolCall": {"functionCalls": [{"id": "c1", "name": "web_search", "args": {"query": "weather"}}]}})
        self.assertEqual(call, [{"type": "tool_call", "id": "c1", "name": "web_search", "arguments": {"query": "weather"}}])
        reply = adapter.tool_result_messages("c1", "web_search", {"results": []})
        self.assertEqual(reply[0]["toolResponse"]["functionResponses"][0]["id"], "c1")
        events = adapter.translate({"serverContent": {"groundingMetadata": {"groundingChunks": [
            {"web": {"uri": "https://example.com/a", "title": "A"}}, {"web": {"uri": "javascript:alert(1)"}}, {"x": 1}]},
            "turnComplete": True}})
        self.assertEqual(events[0], {"type": "sources", "sources": [{"url": "https://example.com/a", "title": "A"}]})
        self.assertEqual(events[1]["type"], "turn_complete")

    def test_openai_realtime_attaches_one_fresh_frame_per_turn_and_searches_with_memuai(self) -> None:
        adapter = realtime_live.build_adapter(config("openai-realtime"))
        self.assertEqual((adapter.video_mode, adapter.search_mode), ("turn", "tool"))
        session = adapter.setup_messages()[0]["session"]
        self.assertEqual([tool["name"] for tool in session["tools"]], ["web_search", "open_page"])
        self.assertEqual(session["tools"][0]["type"], "function")
        self.assertEqual(adapter.video_messages("AAAA"), [], "the frame waits for the user's turn")
        adapter.translate({"type": "input_audio_buffer.speech_started"})
        sent = adapter.drain()
        self.assertEqual(sent[0]["item"]["content"][0], {"type": "input_image", "image_url": "data:image/jpeg;base64,AAAA"})
        adapter.translate({"type": "input_audio_buffer.speech_started"})
        self.assertEqual(adapter.drain(), [], "the same frame is not sent twice")
        adapter.video_messages("BBBB")
        typed = adapter.text_messages("what is this?")
        self.assertEqual([item["type"] for item in typed],
                         ["conversation.item.create", "conversation.item.create", "response.create"])
        self.assertIn("BBBB", typed[0]["item"]["content"][0]["image_url"])
        adapter.video_messages("CCCC")
        adapter.drop_frame()
        self.assertEqual(adapter.text_messages("and now?")[0]["item"]["content"][0]["type"], "input_text",
                         "nothing is attached once sharing stopped")

        call = adapter.translate({"type": "response.function_call_arguments.done", "call_id": "call_1",
                                  "name": "web_search", "arguments": '{"query": "rates"}'})
        self.assertEqual(call, [{"type": "tool_call", "id": "call_1", "name": "web_search", "arguments": {"query": "rates"}}])
        reply = adapter.tool_result_messages("call_1", "web_search", {"results": [{"url": "https://e.com"}]})
        self.assertEqual(reply[0]["item"], {"type": "function_call_output", "call_id": "call_1",
                                            "output": '{"results": [{"url": "https://e.com"}]}'})
        self.assertEqual(reply[1], {"type": "response.create"})

    def test_a_server_that_refuses_tools_keeps_the_conversation(self) -> None:
        adapter = realtime_live.build_adapter(config("xai"))
        self.assertEqual(adapter.video_mode, "", "Grok Voice takes sound only")
        self.assertEqual(adapter.setup_messages()[0]["session"]["tools"], [{"type": "web_search"}, {"type": "x_search"}])
        out = adapter.translate({"type": "error", "error": {"message": "Unknown tool type web_search"}})
        self.assertEqual(out, [{"type": "search_mode", "search": "tool"}])
        again = adapter.drain()
        self.assertEqual(again[0]["type"], "session.update")
        self.assertEqual(again[0]["session"]["tools"][0]["type"], "function")
        other = adapter.translate({"type": "error", "error": {"message": "rate limited"}})
        self.assertEqual(other[0]["type"], "error", "unrelated errors still reach the user")

    def test_search_can_be_switched_off_and_gpt_live_delegates(self) -> None:
        quiet = realtime_live.build_adapter(config("gemini", voice_realtime_search=False))
        self.assertEqual(quiet.search_mode, "")
        self.assertNotIn("tools", quiet.setup_messages()[0]["setup"])
        self.assertNotIn("tools", realtime_live.build_adapter(
            config("openai-realtime", voice_realtime_search=False)).setup_messages()[0]["session"])
        live = realtime_live.build_adapter(config("openai-live"))
        self.assertEqual((live.video_mode, live.search_mode), ("delegate", "delegate"))
        self.assertIn("delegate the request", live.setup_messages()[0]["session"]["instructions"])
        self.assertEqual(live.video_messages("AAAA"), [])
        self.assertEqual(live.frame, "AAAA", "the frame is kept for the delegated model")
        self.assertTrue(pgdata._normalize_preferences({})["voice_realtime_search"])
        self.assertFalse(pgdata._normalize_preferences({"voice_realtime_search": False})["voice_realtime_search"])

class FakeGemini:
    """Gemini Live on localhost: a free-tier key that has no Google Search quota."""

    def __init__(self) -> None:
        self.seen: list = []
        self.setups: list = []
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.stop: dict = {}
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        self.port = probe.getsockname()[1]
        probe.close()
        self.thread = threading.Thread(target=lambda: self.loop.run_until_complete(self.main()), daemon=True)

    async def handler(self, connection) -> None:
        async for raw in connection:
            event = json.loads(raw)
            self.seen.append(event)
            if "setup" in event:
                self.setups.append(event["setup"])
                if any("googleSearch" in tool for tool in event["setup"].get("tools") or []):
                    await connection.close(1011, "You exceeded your current quota, please check your plan")
                    return
                await connection.send(json.dumps({"setupComplete": {}}).encode())
            elif "clientContent" in event:
                await connection.send(json.dumps({"toolCall": {"functionCalls": [
                    {"id": "call-7", "name": "web_search", "args": {"query": "курс доллара"}}]}}))
            elif "toolResponse" in event:
                await connection.send(json.dumps({"serverContent": {
                    "modelTurn": {"parts": [{"inlineData": {"mimeType": "audio/pcm;rate=24000", "data": "UUU="}}]},
                    "outputTranscription": {"text": "Доллар стоит девяносто."}, "turnComplete": True}}))

    async def main(self) -> None:
        from websockets.asyncio.server import serve
        self.stop["future"] = asyncio.get_running_loop().create_future()
        async with serve(self.handler, "127.0.0.1", self.port):
            self.ready.set()
            await self.stop["future"]

    def __enter__(self) -> "FakeGemini":
        self.thread.start()
        assert self.ready.wait(10)
        return self

    def __exit__(self, *_exc) -> None:
        self.loop.call_soon_threadsafe(self.stop["future"].set_result, None)
        self.thread.join(timeout=10)
        self.loop.close()

class LiveRelayTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self._patches = [patch.object(pgdata, "MEMU_DIR", Path(self._temp.name)),
                         patch.dict(os.environ, {"MEMUAI_PROXY_URL": "http://127.0.0.1:9"})]
        for item in self._patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self._patches):
            item.stop()
        self._temp.cleanup()

    def session(self, client):
        return client.websocket_connect("ws://127.0.0.1/api/voice/realtime", headers={"origin": "http://127.0.0.1"})

    def test_frames_reach_the_model_and_a_refused_google_search_becomes_memuai_search(self) -> None:
        from app.playground_api import create_app
        searches: list = []

        async def fake_search(query, **_kwargs):
            searches.append(query)
            return [{"title": "ЦБ РФ", "url": "https://cbr.example/rates", "snippet": "90 руб."}]

        with FakeGemini() as gemini, patch.object(websearch, "search", fake_search):
            pgdata.save_preferences({"voice_realtime_enabled": True, "voice_realtime_provider": "custom-gemini",
                                     "voice_realtime_base_url": f"ws://127.0.0.1:{gemini.port}/live",
                                     "voice_realtime_model": "gemini-fake-live"})
            with TestClient(create_app(), base_url="http://127.0.0.1") as client:
                with self.session(client) as ws:
                    ws.send_text(json.dumps({"type": "start", "instructions": "Говори кратко."}))
                    ready = json.loads(ws.receive_text())
                    self.assertEqual((ready["type"], ready["video"], ready["search"]), ("ready", "stream", "tool"))
                    ws.send_text(json.dumps({"type": "video", "mime": "image/jpeg", "data": "SlBFRw=="}))
                    ws.send_text(json.dumps({"type": "video", "data": "x" * (realtime_live.MAX_FRAME_CHARS + 1)}))
                    ws.send_text(json.dumps({"type": "text", "text": "Какой курс доллара?"}))
                    events = []
                    while not events or events[-1]["type"] != "turn_complete":
                        events.append(json.loads(ws.receive_text()))
                    ws.send_text(json.dumps({"type": "end"}))
                kinds = [event["type"] for event in events]
                self.assertEqual(kinds[:2], ["searching", "sources"])
                self.assertEqual(events[0]["query"], "курс доллара")
                self.assertEqual(events[1]["sources"], [{"url": "https://cbr.example/rates", "title": "ЦБ РФ"}])
                self.assertIn({"type": "searching", "active": False}, events)
                self.assertIn({"type": "assistant_transcript", "delta": "Доллар стоит девяносто."}, events)
                self.assertEqual(searches, ["курс доллара"])

                with self.session(client) as ws:
                    ws.send_text(json.dumps({"type": "start", "instructions": "x"}))
                    self.assertEqual(json.loads(ws.receive_text())["search"], "tool")
                    ws.send_text(json.dumps({"type": "end"}))

            self.assertEqual(len(gemini.setups), 3, "refused once, then the working setup is remembered")
            self.assertIn("googleSearch", gemini.setups[0]["tools"][0])
            self.assertIn("functionDeclarations", gemini.setups[1]["tools"][0])
            self.assertIn("functionDeclarations", gemini.setups[2]["tools"][0])
            frames = [event["realtimeInput"]["video"] for event in gemini.seen
                      if "video" in (event.get("realtimeInput") or {})]
            self.assertEqual(frames, [{"data": "SlBFRw==", "mimeType": "image/jpeg"}], "the oversized frame is dropped")
            answer = next(event["toolResponse"]["functionResponses"][0] for event in gemini.seen if "toolResponse" in event)
            self.assertEqual((answer["id"], answer["name"]), ("call-7", "web_search"))
            self.assertEqual(answer["response"]["result"]["results"][0]["url"], "https://cbr.example/rates")
            self.assertIn("Untrusted web text", answer["response"]["result"]["note"])

    def test_the_window_can_ask_memuai_to_capture_the_screen(self) -> None:
        from app import playground_api
        from app.playground_api import create_app

        with FakeGemini() as gemini, patch("PIL.ImageGrab.grab") as grab:
            from PIL import Image
            grab.return_value = Image.new("RGB", (2560, 1440), "navy")
            pgdata.save_preferences({"voice_realtime_enabled": True, "voice_realtime_provider": "custom-gemini",
                                     "voice_realtime_base_url": f"ws://127.0.0.1:{gemini.port}/live",
                                     "voice_realtime_model": "gemini-fake-live", "voice_realtime_search": False})
            with TestClient(create_app(), base_url="http://127.0.0.1") as client, self.session(client) as ws:
                ws.send_text(json.dumps({"type": "start", "instructions": "x"}))
                self.assertEqual(json.loads(ws.receive_text())["search"], "")
                ws.send_text(json.dumps({"type": "screen", "on": True}))
                preview = json.loads(ws.receive_text())
                self.assertEqual(preview["type"], "preview")
                self.assertLess(len(preview["data"]), 20000, "the window gets a thumbnail, not the frame")
                ws.send_text(json.dumps({"type": "video_end"}))
                ws.send_text(json.dumps({"type": "end"}))
            frames = [event["realtimeInput"]["video"] for event in gemini.seen
                      if "video" in (event.get("realtimeInput") or {})]
            self.assertTrue(frames)
            self.assertEqual(frames[0]["mimeType"], "image/jpeg")
            import base64
            import io
            self.assertEqual(Image.open(io.BytesIO(base64.b64decode(frames[0]["data"]))).size, (1280, 720))
            self.assertNotIn("tools", gemini.setups[0])
        self.assertTrue(hasattr(playground_api, "create_app"))

class LiveWindowTest(unittest.TestCase):
    def test_the_window_has_camera_screen_and_search_wiring(self) -> None:
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        for node in ('id="live-camera"', 'id="live-screen"', 'id="live-preview-video"', 'id="live-search"'):
            self.assertIn(node, html)
        realtime = (STATIC / "realtime.js").read_text(encoding="utf-8")
        for needle in ('type: "video"', 'type: "video_end"', 'type: "screen", on: true', "getDisplayMedia",
                       "bufferedAmount", 'case "sources"', 'case "searching"', "setVideo"):
            self.assertIn(needle, realtime)
        app = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn('MemuRealtime.setVideo("camera")', app)
        self.assertIn('MemuRealtime.setVideo("screen")', app)
        self.assertIn("voice_realtime_search", app)
        self.assertIn("message.searchUsed", app)

    def test_live_models_get_the_chosen_personality(self) -> None:
        app = (STATIC / "app.js").read_text(encoding="utf-8")
        body = app[app.index("function buildRealtimeInstructions()"):]
        body = body[:body.index("\nfunction pushLiveTurn")]
        for needle in ('buildLivePersonalityInstructions()', "memePromptBlock", "custom_system_prompt",
                       "chat_specific_instructions"):
            self.assertIn(needle, body)
        self.assertNotIn('pref("ai_personality"', body)
        persona = app[app.index("function buildLivePersonalityInstructions()"):app.index("function buildRealtimeInstructions()")]
        self.assertIn('pref("voice_live_primary_personality"', persona)
        self.assertIn("selected Live style overrides", persona)
        self.assertIn("PRIMARY BEHAVIOR", persona)

    def test_the_local_page_may_use_the_camera_without_a_browser_prompt(self) -> None:
        from app import desktop
        self.assertIn("Camera", desktop._LOCAL_PERMISSIONS)
        self.assertNotIn("Geolocation", desktop._LOCAL_PERMISSIONS)

if __name__ == "__main__":
    unittest.main()
