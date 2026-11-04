"""One skill library for every Memu app: places, defaults, prompts and the load protocol (app/skills_scope.py)."""
import json
import re
import threading
import unittest
from pathlib import Path
from unittest import mock

from app import code_skills, skills_scope

ROOT = Path(__file__).resolve().parents[1]


def _state(rows, removed=()):
    return {"skills": rows, "version": 9, "removed": list(removed)}


class Library(unittest.TestCase):
    def test_builtin_skills_come_from_skills_js(self):
        rows = skills_scope.builtin_skills()
        ids = [row["id"] for row in rows]
        self.assertGreaterEqual(len(rows), 20)
        self.assertEqual(len(ids), len(set(ids)))
        for wanted in ("code_review", "memuai_guide", "translation"):
            self.assertIn(wanted, ids)
        # Panels registers itself in panels.js and is chat-only, so the other apps never need it
        self.assertIn('SKILL_ID = "memuai_panels"', (ROOT / "app" / "pgstatic" / "panels.js").read_text(encoding="utf-8"))
        self.assertTrue(all(str(row.get("prompt") or "").strip() for row in rows if row["id"] != "memuai_guide"))

    def test_defaults(self):
        self.assertTrue(skills_scope.works_in({"id": "translation"}, "note"))
        self.assertTrue(skills_scope.works_in({"id": "translation"}, "mod:meet"))
        self.assertFalse(skills_scope.works_in({"id": "translation"}, "mod:music"))
        for place in ("files", "note", "code", "cli", "mod:meet"):
            self.assertFalse(skills_scope.works_in({"id": "memuai_panels"}, place), place)
            self.assertFalse(skills_scope.works_in({"id": "memuai_guide"}, place), place)
        self.assertTrue(skills_scope.works_in({"id": "memuai_panels"}, "chat"))
        # MemuCode has its own review/debug/security/commit playbooks
        for twin in skills_scope.CODE_TWINS:
            self.assertFalse(skills_scope.works_in({"id": twin}, "code"))
            self.assertFalse(skills_scope.works_in({"id": twin}, "cli"))
            self.assertTrue(skills_scope.works_in({"id": twin}, "files"))

    def test_own_choice_wins_but_chat_only_stays_in_chat(self):
        row = {"id": "translation", "places": {"note": False, "mod:music": True}}
        self.assertFalse(skills_scope.works_in(row, "note"))
        self.assertTrue(skills_scope.works_in(row, "mod:music"))
        self.assertTrue(skills_scope.works_in(row, "files"))
        self.assertFalse(skills_scope.works_in({"id": "memuai_panels", "places": {"files": True}}, "files"))
        self.assertTrue(skills_scope.works_in({"id": "code_review", "places": {"code": True}}, "code"))

    def test_library_mirrors_the_memuai_window(self):
        rows = [{"id": "translation", "enabled": False},
                {"id": "summarize", "enabled": True, "places": {"note": False}},
                {"id": "custom_1", "custom": True, "enabled": True, "name": "Mine", "desc": "My own", "prompt": "Do it",
                 "places": {"files": False}},
                {"id": "custom_off", "custom": True, "enabled": False, "name": "Off", "prompt": "x"}]
        state = _state(rows, removed=["slides"])
        lib = {row["id"]: row for row in skills_scope.library(state)}
        self.assertNotIn("slides", lib)
        self.assertFalse(lib["translation"]["enabled"])
        self.assertEqual(lib["summarize"]["places"], {"note": False})
        self.assertIn("custom_1", lib)
        note = {row["id"] for row in skills_scope.for_place("note", state)}
        files = {row["id"] for row in skills_scope.for_place("files", state)}
        self.assertNotIn("translation", note)
        self.assertNotIn("summarize", note)
        self.assertIn("summarize", files)
        self.assertIn("custom_1", note)
        self.assertNotIn("custom_1", files)
        self.assertNotIn("custom_off", note)
        self.assertFalse(skills_scope.for_place("../etc", state))

    def test_an_old_or_missing_library_means_all_builtins(self):
        self.assertEqual(len(skills_scope.library({})), len(skills_scope.builtin_skills()))
        old = {"skills": [{"id": "translation", "enabled": False}], "version": 2}
        self.assertTrue(next(row for row in skills_scope.library(old) if row["id"] == "translation")["enabled"])


class Prompt(unittest.TestCase):
    def test_prompt_block_lists_only_what_works_here(self):
        block = skills_scope.prompt_block("note", state={})
        self.assertIn("<skills>", block)
        self.assertIn("- translation:", block)
        self.assertNotIn("memuai_panels", block)
        self.assertNotIn("memuai_guide", block)
        self.assertIn("```memuai-skill", block)
        self.assertEqual(skills_scope.prompt_block("mod:music", state={}), "")
        self.assertIn("<skills>", skills_scope.prompt_block("mod:meet", state={}))
        tool = skills_scope.prompt_block("files", protocol="tool", state={})
        self.assertNotIn("skills_protocol", tool)

    def test_instructions_and_follow_up(self):
        text, loaded = skills_scope.instructions(["translation", "memuai_panels", "nope"], "note", state={})
        self.assertEqual(loaded, ["translation"])
        self.assertIn('<skill id="translation"', text)
        text, loaded = skills_scope.instructions(["memuai_panels"], "note", state={})
        self.assertEqual(loaded, [])
        self.assertIn('status="not_found"', text)
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
        again, loaded = skills_scope.follow_up(messages, ["summarize"], "note", "Sure.", state={})
        self.assertEqual(loaded, ["summarize"])
        self.assertEqual(len(again), 4)
        self.assertEqual(again[2]["role"], "assistant")
        self.assertIn("```memuai-skill", again[2]["content"])
        self.assertIn("<skill_instructions>", again[3]["content"])
        self.assertEqual(len(messages), 2)

    def test_attach_inserts_or_extends_the_system_message(self):
        messages = [{"role": "user", "content": "hi"}]
        self.assertTrue(skills_scope.attach(messages, "mod:meet", state={}))
        self.assertEqual(messages[0]["role"], "system")
        messages = [{"role": "system", "content": "Base"}, {"role": "user", "content": "hi"}]
        skills_scope.attach(messages, "mod:learn", state={})
        self.assertTrue(messages[0]["content"].startswith("Base\n\n<skills>"))
        self.assertFalse(skills_scope.attach([{"role": "user", "content": "x"}], "mod:music", state={}))

    def test_parse_request(self):
        self.assertEqual(skills_scope.parse_request('{"skills": ["a", "b", "a"]}'), ["a", "b"])
        self.assertEqual(skills_scope.parse_request('{"skill": "x"}'), ["x"])
        self.assertEqual(skills_scope.parse_request('["x","y","z","w","v"]'), ["x", "y", "z", "w"])
        self.assertEqual(skills_scope.parse_request("not json"), [])


class Gate(unittest.TestCase):
    def feed_all(self, chunks):
        gate = skills_scope.SkillGate()
        shown = "".join(gate.feed(chunk) for chunk in chunks)
        return gate, shown + gate.finish()

    def test_plain_text_passes_through_even_with_backticks(self):
        gate, shown = self.feed_all(["Here is code:\n``", "`python\nprint(1)\n```", " done"])
        self.assertEqual(shown, "Here is code:\n```python\nprint(1)\n``` done")
        self.assertEqual(gate.requested, [])

    def test_a_split_block_is_held_back_and_parsed(self):
        chunks = ["Let me check. ", "``", "`memu", "ai-skill\n{\"skills\": [\"code_", "review\"]}\n`", "``", "IGNORED"]
        gate = skills_scope.SkillGate()
        shown = ""
        for chunk in chunks:
            shown += gate.feed(chunk)
            if gate.done:
                break
        self.assertEqual(shown, "Let me check. ")
        self.assertEqual(gate.requested, ["code_review"])
        self.assertEqual(gate.shown, "Let me check. ")

    def test_an_unfinished_block_is_dropped(self):
        gate, shown = self.feed_all(["ok ```memuai-skill\n{\"skills\": ["])
        self.assertEqual(shown, "ok ")
        self.assertEqual(gate.requested, [])


class Parity(unittest.TestCase):
    def test_app_js_uses_the_same_rules(self):
        src = (ROOT / "app" / "pgstatic" / "app.js").read_text(encoding="utf-8")
        places = json.loads(re.search(r"const SKILL_PLACES = (\[[^\]]*\]);", src).group(1))
        modules = json.loads(re.search(r"const SKILL_DEFAULT_MODULES = (\[[^\]]*\]);", src).group(1))
        twins = re.search(r"const SKILL_CODE_TWINS = \{([^}]*)\};", src).group(1)
        chat_only = re.search(r"const SKILL_CHAT_ONLY = \{(.*?)\n\};", src, re.S).group(1)
        self.assertEqual(tuple(places), skills_scope.CORE_PLACES)
        self.assertEqual(tuple(modules), skills_scope.DEFAULT_MODULES)
        self.assertEqual(dict(re.findall(r"(\w+): \"(\w+)\"", twins)), skills_scope.CODE_TWINS)
        self.assertEqual(set(re.findall(r"^\s*(\w+): \[", chat_only, re.M)), set(skills_scope.CHAT_ONLY))
        self.assertIn('skillWorksIn(s, "chat")', src)
        ui = (ROOT / "app" / "pgstatic" / "skills-ui.js").read_text(encoding="utf-8")
        for needle in ("data-place", "placesView", "MemuAI Proxy"):
            self.assertIn(needle, ui)


class Apps(unittest.TestCase):
    def setUp(self):
        self.patch = mock.patch.object(skills_scope, "_state", return_value=_state(
            [{"id": "translation", "enabled": True, "places": {"code": False, "cli": True}},
             {"id": "summarize", "enabled": False}]))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_memucode_window_and_cli_get_library_rows(self):
        code = {row["id"]: row for row in code_skills.catalogue("", include_prompt=True)}
        cli = {row["id"]: row for row in code_skills.catalogue("", include_prompt=True, place="cli")}
        none = {row["id"] for row in code_skills.catalogue("", place="")}
        self.assertIn("review", code)
        self.assertEqual(code["data_analysis"]["source"], "library")
        self.assertTrue(code["data_analysis"]["prompt"])
        self.assertNotIn("translation", code)
        self.assertIn("translation", cli)
        self.assertNotIn("summarize", cli)
        self.assertNotIn("code_review", code)  # the MemuCode twin
        self.assertNotIn("data_analysis", none)
        self.assertTrue(code_skills.prompt_for("", "data_analysis"))
        self.assertEqual(code_skills.prompt_for("", "translation"), "")
        self.assertTrue(code_skills.prompt_for("", "translation", place="cli"))

    def test_memufiles_assistant(self):
        from app.memufiles import assistant
        prompt = assistant.system_prompt("/tmp", "en")
        self.assertIn("<skills>", prompt)
        self.assertIn("load_skill", prompt)
        self.assertNotIn("- summarize:", prompt)
        result = assistant.load_skill({"ids": ["translation", "memuai_panels"]})
        self.assertEqual(result["skills"], ["translation"])
        self.assertIn('<skill id="translation"', result["instructions"])

    def test_memunote_prompts(self):
        from app.memunote import prompts
        chat = prompts.build_messages({"action": "chat", "messages": [{"role": "user", "content": "hi"}]}, {}, "en", False)
        self.assertTrue(chat["skills"])
        self.assertIn("<skills>", chat["messages"][0]["content"])
        agent = prompts.build_messages({"action": "agent", "instruction": "fix", "context": {"text": "a"}}, {}, "en", False)
        self.assertIn("<skills>", agent["messages"][0]["content"])
        auto = prompts.build_messages({"action": "autocomplete", "before": "Hel"}, {}, "en", False)
        self.assertFalse(auto["skills"])
        self.assertNotIn("<skills>", auto["messages"][0]["content"])


class NoteStream(unittest.TestCase):
    """MemuNote: the hidden block never reaches the page, the playbook goes into the second pass."""

    def setUp(self):
        from tests.round32_slides_test import _ServerCase
        self.case = _ServerCase("run")
        self.case.setUp()
        self.patch = mock.patch.object(skills_scope, "_state", return_value={})
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.case.tearDown()

    def test_skill_request_then_answer(self):
        calls = []

        def fake_stream(base, payload, cancel, on_conn=None):
            calls.append([dict(m) for m in payload["messages"]])
            if len(calls) == 1:
                for chunk in ["One moment. ", "```memuai", "-skill\n{\"skills\": [\"translation\"]}\n```", "LEAK"]:
                    yield {"t": "delta", "text": chunk}
            else:
                for chunk in ["Bonjour", " le monde"]:
                    yield {"t": "delta", "text": chunk}

        with mock.patch("app.memunote.ai.discover", return_value="http://127.0.0.1:9"), \
                mock.patch("app.memunote.ai.stream_chat", side_effect=fake_stream):
            response, raw = self.case.call("POST", "/api/ai_chat", {
                "model": "fake", "id": "skill-1", "action": "chat", "memory": False,
                "messages": [{"role": "user", "content": "Translate 'hello world' to French"}]}, raw=True)
        self.assertEqual(response.status, 200)
        events = [json.loads(line[6:]) for line in raw.decode("utf-8").splitlines() if line.startswith("data: ")]
        text = "".join(e["text"] for e in events if e["t"] == "delta")
        self.assertEqual(text, "One moment. Bonjour le monde")
        self.assertNotIn("LEAK", text)
        self.assertIn({"t": "skill", "ids": ["translation"]}, events)
        self.assertEqual(events[-1]["t"], "done")
        self.assertEqual(len(calls), 2)
        self.assertIn("<skill_instructions>", calls[1][-1]["content"])
        self.assertIn("<skills>", calls[0][0]["content"])


class _FakeProxy:
    """A tiny MemuAI Proxy: the first answer asks for a skill, the second one answers."""

    def __init__(self):
        import http.server
        self.calls = []
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                owner.calls.append(body.get("messages") or [])
                if len(owner.calls) % 2 == 1:
                    chunks = ["Sure. ", "```memuai-skill\n{\"skills\": [\"translation\"]}", "\n```", "LEAK"]
                else:
                    chunks = ["Bonjour", " le monde"]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for chunk in chunks:
                    item = {"choices": [{"delta": {"content": chunk}}]}
                    self.wfile.write(b"data: " + json.dumps(item).encode() + b"\n\n")
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class ModuleBridge(unittest.TestCase):
    """Memu.stream / Memu.request of a module get the skills switched on for that module."""

    def setUp(self):
        import tempfile
        from app import pgdata
        self.proxy = _FakeProxy()
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        (root / "data").mkdir()
        self._patches = [
            mock.patch.object(pgdata, "MEMU_DIR", root / "data"),
            mock.patch("app.modules_host.default_root", return_value=root / "modules"),
            mock.patch("pathlib.Path.home", return_value=root / "home"),
            mock.patch.dict("os.environ", {"MEMUAI_PROXY_URL": self.proxy.url}),
            mock.patch.object(skills_scope, "_state", return_value={}),
        ]
        for item in self._patches:
            item.start()

    def tearDown(self):
        for item in reversed(self._patches):
            item.stop()
        self.proxy.close()
        self._temp.cleanup()

    def test_stream_and_request(self):
        from fastapi.testclient import TestClient
        from app.playground_api import create_app
        with TestClient(create_app(), base_url="http://127.0.0.1") as client:
            for module_id in ("meet", "music"):
                answer = client.post("/api/pg/modules/configure", json={"id": module_id, "enabled": True})
                self.assertEqual(answer.status_code, 200, answer.text)
            stream = client.post("/api/pg/modules/stream", json={"module_id": "meet", "model": "fake", "text": "Translate hello world"})
            self.assertEqual(stream.status_code, 200, stream.text)
            events = [json.loads(line[6:]) for line in stream.text.splitlines() if line.startswith("data: ")]
            text = "".join(e.get("delta", "") for e in events if e.get("event") == "delta")
            self.assertEqual(text, "Sure. \n\nBonjour le monde")
            self.assertIn({"status": "skill"}, events)
            self.assertEqual(events[-1]["event"], "done")
            self.assertEqual(events[-1]["result"]["text"], "Sure. \n\nBonjour le monde")
            self.assertEqual(len(self.proxy.calls), 2)
            self.assertIn("<skills>", self.proxy.calls[0][0]["content"])
            self.assertIn("<skill_instructions>", self.proxy.calls[1][-1]["content"])

            request = client.post("/api/pg/modules/request", json={"module_id": "meet", "model": "fake", "text": "Translate"})
            self.assertEqual(request.status_code, 200, request.text)
            self.assertEqual(request.json()["text"], "Sure.\n\nBonjour le monde")
            self.assertEqual(request.json()["skills"], ["translation"])
            self.assertEqual(len(self.proxy.calls), 4)

            # MemuMusic is not a default place: no catalogue, one pass, the raw answer passes through
            plain = client.post("/api/pg/modules/request", json={"module_id": "music", "model": "fake", "text": "x"})
            self.assertEqual(plain.status_code, 200, plain.text)
            self.assertEqual(len(self.proxy.calls), 5)
            self.assertFalse(any("<skills>" in str(m.get("content")) for m in self.proxy.calls[4]))


if __name__ == "__main__":
    unittest.main()
