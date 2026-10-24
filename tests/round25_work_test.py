"""Round 25 (b3-work-agents): Work tools and agents, MCP client, deep research, web pages, YouTube analysis.

Everything runs offline: models, MCP servers and YouTube are scripted stand-ins.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from app import deep_research, mcp_client, websearch, work_agents, work_tools
from app import youtube_analysis as yt

ROOT = Path(__file__).resolve().parents[1]
VID = "dQw4w9WgXcQ"


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- Work tools
class WorkToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp()).resolve()
        (self.root / "src").mkdir()
        (self.root / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")

    def test_deleting_a_link_keeps_its_target(self) -> None:
        os.symlink(self.root / "src", self.root / "lib", target_is_directory=True)
        result = work_tools.run("work_delete", self.root, {"path": "lib"})
        self.assertTrue(result["deleted"])
        self.assertFalse(os.path.lexists(self.root / "lib"))
        self.assertTrue((self.root / "src" / "main.py").is_file())    # used to remove the whole src folder

    def test_moving_a_link_moves_the_link(self) -> None:
        os.symlink(self.root / "src", self.root / "lib", target_is_directory=True)
        work_tools.run("work_move", self.root, {"path": "lib", "to": "vendor_lib"})
        self.assertTrue((self.root / "vendor_lib").is_symlink())
        self.assertTrue((self.root / "src" / "main.py").is_file())

    def test_move_never_overwrites_and_never_into_itself(self) -> None:
        (self.root / "notes.txt").write_text("notes", encoding="utf-8")
        with self.assertRaises(work_tools.WorkError):
            work_tools.run("work_move", self.root, {"path": "notes.txt", "to": "src/main.py"})
        self.assertEqual((self.root / "src" / "main.py").read_text(encoding="utf-8"), "print('hi')\n")
        with self.assertRaises(work_tools.WorkError):
            work_tools.run("work_move", self.root, {"path": "src", "to": "src/inner"})
        self.assertEqual(work_tools.run("work_move", self.root, {"path": "notes.txt", "to": "docs/n.txt"})["to"],
                         "docs/n.txt")

    def test_mkdir_over_a_file_is_a_tool_error(self) -> None:
        with self.assertRaises(work_tools.WorkError):
            work_tools.run("work_mkdir", self.root, {"path": "src/main.py"})

    def test_bad_arguments_are_tool_errors_not_http_errors(self) -> None:
        with self.assertRaises(work_tools.WorkError):
            work_tools.run("work_tree", self.root, {"limit": "many"})
        with self.assertRaises(work_tools.WorkError):
            work_tools.run("work_read", self.root, {"path": "src/main.py", "start_line": "x"})

    def test_catastrophic_regex_is_refused_quickly(self) -> None:
        (self.root / "long.txt").write_text("a" * 5000 + "!\n", encoding="utf-8")
        for pattern in (r"(a+)+$", r"(?:\w*)*x", r"(\w+\s?)+$"):
            with self.assertRaises(work_tools.WorkError, msg=pattern):
                work_tools.run("work_search", self.root, {"query": pattern, "regex": True})
        for pattern in (r"def \w+\(", r"(ab|cd)+", r"\d+\.\d+", r"foo.*bar", r"(a{2,3})+"):
            work_tools.run("work_search", self.root, {"query": pattern, "regex": True})
        # A literal query is escaped: «(a+)+» as plain text is fine.
        work_tools.run("work_search", self.root, {"query": "(a+)+"})


# ---------------------------------------------------------------- agents
class AgentsTest(unittest.TestCase):
    def test_an_agent_stopped_before_its_first_step_frees_its_slot(self) -> None:
        async def complete(_model, _messages):
            await asyncio.sleep(0.05)
            return "report"

        async def tool(_name, _args, _job):
            return {}

        async def scenario():
            pool = work_agents.AgentPool(complete, tool)
            job = pool.spawn("s", title="t", task="x", model="m", limit=1)
            pool.cancel("s", job.id)
            await asyncio.sleep(0.05)
            second = pool.spawn("s", title="t2", task="y", model="m", limit=1)   # used to raise «1 agents running»
            await asyncio.wait_for(second.handle, 2)
            return job, second

        job, second = run(scenario())
        self.assertEqual(job.status, "cancelled")
        self.assertTrue(job.finished)
        self.assertEqual(second.status, "done")

    def test_an_answer_cut_inside_the_tool_block_is_not_a_report(self) -> None:
        answers = ['Reading it now\n```memuai-tool\n[{"tool": "work_read", "args": {"path": "a',
                   "Final report: a.py has 3 lines."]
        seen = []

        async def complete(_model, messages):
            seen.append(messages[-1]["content"])
            return answers.pop(0)

        async def tool(_name, _args, _job):
            return {}

        async def scenario():
            pool = work_agents.AgentPool(complete, tool)
            job = pool.spawn("s", title="t", task="x", model="m")
            await asyncio.wait_for(job.handle, 2)
            return job

        job = run(scenario())
        self.assertEqual(job.report, "Final report: a.py has 3 lines.")
        self.assertIn("ended in the middle", seen[-1])

    def test_old_tool_results_are_shortened_to_fit_the_context(self) -> None:
        sizes = []
        big = "x" * 39_000

        async def complete(_model, messages):
            sizes.append(sum(len(str(m["content"])) for m in messages))
            if len(sizes) < 12:
                return '```memuai-tool\n[{"tool": "work_read", "args": {"path": "a.py"}}]\n```'
            return "done"

        async def tool(name, _args, _job):
            return {"tool": name, "text": big}

        async def scenario():
            pool = work_agents.AgentPool(complete, tool)
            job = pool.spawn("s", title="t", task="x", model="m")
            await asyncio.wait_for(job.handle, 5)
            return job

        job = run(scenario())
        self.assertEqual(job.report, "done")
        self.assertLess(max(sizes), work_agents.HISTORY_CHARS + 45_000)

    def test_extra_calls_are_reported_not_dropped_silently(self) -> None:
        calls = [{"tool": "work_read", "args": {"path": f"f{i}.py"}} for i in range(10)]
        answers = ["```memuai-tool\n" + json.dumps(calls) + "\n```", "ok"]
        seen = []

        async def complete(_model, messages):
            seen.append(messages[-1]["content"])
            return answers.pop(0)

        async def tool(name, _args, _job):
            return {"tool": name}

        async def scenario():
            pool = work_agents.AgentPool(complete, tool)
            job = pool.spawn("s", title="t", task="x", model="m")
            await asyncio.wait_for(job.handle, 2)
            return job

        job = run(scenario())
        self.assertEqual(len(job.steps), work_agents.MAX_CALLS)
        self.assertIn("were skipped", seen[-1])

    def test_window_marks_cards_of_a_restarted_app_interrupted(self) -> None:
        done = subprocess.run([shutil.which("node") or "node", str(ROOT / "tests" / "round25_work_runner.cjs")],
                              capture_output=True, text=True, encoding="utf-8", timeout=60)
        lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
        self.assertTrue(lines, done.stderr[-1500:])
        out = json.loads(lines[-1])
        self.assertNotIn("error", out, out.get("error"))
        self.assertEqual((out["stale"]["status"], out["stale"]["delivered"], out["stale"]["resumed"]),
                         ("cancelled", True, 0))
        self.assertFalse(out["stale"]["card"]["actions"])            # no «Stop the agents» for a dead agent
        self.assertIn("restarted", out["stale"]["error"])
        self.assertEqual((out["freshAfterRace"], out["freshNext"]), ("running", "running"))


# ---------------------------------------------------------------- MCP
FAKE_SERVER = textwrap.dedent(r'''
    import json, sys
    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\n"); sys.stdout.flush()
    pinged = False
    for line in sys.stdin:
        msg = json.loads(line)
        method, rid = msg.get("method"), msg.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2025-06-18", "capabilities": {}}})
        elif method == "tools/list":
            if msg.get("params", {}).get("cursor") == "p2":
                send({"jsonrpc": "2.0", "id": rid, "result": {"tools": [{"name": "second"}]}})
            else:
                send({"jsonrpc": "2.0", "id": rid, "result": {"tools": [{"name": "first"}], "nextCursor": "p2"}})
        elif method == "tools/call":
            name = msg["params"]["name"]
            if name == "ask_back" and not pinged:
                # The server asks the client first, with an id equal to the client's request id.
                send({"jsonrpc": "2.0", "id": rid, "method": "ping"})
                reply = json.loads(sys.stdin.readline())
                pinged = reply.get("result") == {} and reply.get("id") == rid
                send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": "pong ok" if pinged else "no pong"}]}})
            elif name == "string_error":
                send({"jsonrpc": "2.0", "id": rid, "error": "plain text error"})
            elif name == "odd_result":
                send({"jsonrpc": "2.0", "id": rid, "result": ["not", "an", "object"]})
            else:
                send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": name}]}})
''')


class MCPClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "server.py").write_text(FAKE_SERVER, encoding="utf-8")
        self.spec = {"command": sys.executable, "args": [str(self.dir / "server.py")], "env": {}, "cwd": "",
                     "enabled": True, "description": ""}

    def scenario(self, body):
        async def go():
            registry = mcp_client.MCPRegistry()
            with patch.object(mcp_client, "load_config", return_value={"servers": {"t": self.spec}}):
                try:
                    return await body(registry)
                finally:
                    await registry.stop_all()
        return run(go())

    def test_server_requests_are_answered_and_not_taken_for_results(self) -> None:
        async def body(registry):
            server = await registry.server("t")
            names = [tool["name"] for tool in server.tools]
            asked = await registry.call("t", "ask_back", {})
            return names, asked["content"]
        names, content = self.scenario(body)
        self.assertEqual(names, ["first", "second"])                    # paginated tools/list
        self.assertEqual(content, "pong ok")

    def test_malformed_answers_become_readable_errors(self) -> None:
        async def body(registry):
            with self.assertRaises(RuntimeError) as cm:
                await registry.call("t", "string_error", {})
            odd = await registry.call("t", "odd_result", {})
            return str(cm.exception), odd
        message, odd = self.scenario(body)
        self.assertIn("plain text error", message)
        self.assertEqual(odd["content"], "")

    def test_a_timeout_names_the_tool(self) -> None:
        async def body(registry):
            server = await registry.server("t")

            async def never(*_a, **_k):
                await asyncio.sleep(5)
            with patch.object(mcp_client, "CALL_TIMEOUT", 0.05), patch.object(server, "_request", never):
                with self.assertRaises(RuntimeError) as cm:
                    await server.call("slow", {})
            return str(cm.exception)
        self.assertIn("slow", self.scenario(body))


# ---------------------------------------------------------------- deep research
class DeepResearchTest(unittest.TestCase):
    def engine(self, answers):
        async def complete(messages):
            system = messages[0]["content"]
            for key, value in answers.items():
                if key in system:
                    return value() if callable(value) else value
            return "{}"

        async def search(query, _limit):
            return [{"title": f"{query} {i}", "url": f"https://site{i}.example/{'p' * 2100}/{query}",
                     "snippet": "s"} for i in range(8)]

        async def fetch(url, _limit):
            return {"text": "Fact number one with data. " * 20, "title": "T"}

        return deep_research.ResearchEngine(complete=complete, search=search, fetch=fetch,
                                            limits=deep_research.ResearchLimits(max_queries=6, max_rounds=3,
                                                                                max_sources=4))

    def test_string_flags_and_lists_and_long_urls(self) -> None:
        plan = json.dumps({"title": "x", "sub_questions": [{"id": "q1", "question": "Q?", "queries": ["alpha", "beta"]}]})
        reviews = iter([json.dumps({"done": "false", "follow_up_queries": [{"query": "delta", "sub": "q1"}]}),
                        json.dumps({"done": "false", "follow_up_queries": "gamma"})])
        reader = json.dumps({"sources": [{"n": n, "relevance": 2, "findings": ["fact"]} for n in range(1, 20)]})
        engine = self.engine({"planning stage": plan, "review the evidence": lambda: next(reviews, "{}"),
                              "extract evidence": reader})
        result = run(engine.run("topic"))
        queries = [q["query"] for q in result["queries"]]
        # «"done": "false"» used to end the run after one round; a string list became one-letter queries.
        self.assertNotIn("g", queries)
        self.assertIn("delta", queries)
        self.assertGreaterEqual(result["stats"]["rounds"], 2)
        self.assertTrue(result["sources"])                              # URLs over 2000 characters did not crash it


# ---------------------------------------------------------------- web pages
class FetchPageTextTest(unittest.TestCase):
    def test_blank_indented_lines_collapse_and_short_article_falls_back(self) -> None:
        body = ("<html><head><title>Page</title></head><body>\n\t\n\t\n <nav>\n\t\n</nav>"
                "<article><h2>Card one</h2></article><article><h2>Card two</h2></article>"
                + "<p>Real content paragraph.</p>\n\t \n\t\n" * 80 + "</body></html>")
        text = websearch._page_text(body)
        self.assertNotRegex(text, r"\n[ \t]+\n")
        self.assertNotIn("\n\n\n", text)
        article = re.search(websearch._ARTICLE_RE, body).group("body")
        self.assertLess(len(websearch._page_text(article)), 1000)      # fetch_page then reads the whole page
        self.assertIn("Real content paragraph.", text)

    def test_empty_results_are_not_cached(self) -> None:
        calls = []

        async def engine(*_a, **_k):
            calls.append(1)
            return []
        websearch._CACHE.clear()
        with patch.object(websearch, "run_engine", engine), \
                patch.object(websearch, "configured", return_value=("bing", "", "")):
            run(websearch.search("nothing here"))
            run(websearch.search("nothing here"))
        self.assertEqual(len(calls), 2)


# ---------------------------------------------------------------- YouTube
PLAYER = {
    "playabilityStatus": {"status": "OK"},
    "videoDetails": {"videoId": VID, "lengthSeconds": "212"},
    "captions": {"playerCaptionsTracklistRenderer": {"captionTracks": [
        {"baseUrl": "https://www.youtube.com/api/timedtext?v=" + VID + "&lang=en&exp=xpe", "languageCode": "en",
         "name": {"simpleText": "English"}},
    ]}},
}
JSON3 = json.dumps({"events": [{"tStartMs": 0, "segs": [{"utf8": "Hello"}]},
                               {"tStartMs": 65000, "segs": [{"utf8": "World"}]}]})


class YouTubeTest(unittest.TestCase):
    def setUp(self) -> None:
        yt._CACHE.clear()

    def test_watch_page_links_with_an_empty_body_fall_back_to_innertube(self) -> None:
        posts = []

        async def fetch(url):
            if "/watch?" in url:
                return "<script>var ytInitialPlayerResponse = " + json.dumps(PLAYER) + ";</script>"
            if "exp=xpe" in url:
                return ""                                  # what YouTube now sends without a PO token
            if "timedtext" in url and "fmt=json3" in url:
                return JSON3
            if "oembed" in url:
                return json.dumps({"title": "T"})
            raise AssertionError(url)

        async def post(url, payload):
            posts.append((url, payload))
            player = json.loads(json.dumps(PLAYER))
            player["captions"]["playerCaptionsTracklistRenderer"]["captionTracks"][0]["baseUrl"] = \
                "https://www.youtube.com/api/timedtext?v=" + VID + "&lang=en&fmt=srv3"
            return json.dumps(player)

        data = run(yt.transcript(VID, "ru", fetch, use_ytdlp=False, post=post))
        self.assertEqual([s["text"] for s in data["segments"]], ["Hello", "World"])
        self.assertEqual(data["duration"], 212)
        self.assertEqual(posts[0][0], yt.INNERTUBE_PLAYER)
        self.assertEqual(posts[0][1]["videoId"], VID)

    def test_empty_captions_give_a_clear_reason_not_a_json_error(self) -> None:
        async def fetch(url):
            if "/watch?" in url:
                return "<script>var ytInitialPlayerResponse = " + json.dumps(PLAYER) + ";</script>"
            return ""

        with self.assertRaises(yt.TranscriptUnavailable) as cm:
            run(yt.transcript(VID, "ru", fetch, use_ytdlp=False))
        self.assertEqual(cm.exception.code, "fetch")
        self.assertIn("пустые субтитры", str(cm.exception))
        self.assertNotIn("Expecting value", str(cm.exception))

    def test_status_from_the_watch_page_wins(self) -> None:
        async def fetch(url):
            player = {"playabilityStatus": {"status": "LOGIN_REQUIRED"}}
            return "<script>var ytInitialPlayerResponse = " + json.dumps(player) + ";</script>"

        async def post(url, payload):
            return json.dumps({"playabilityStatus": {"status": "OK"}})
        with self.assertRaises(yt.TranscriptUnavailable) as cm:
            run(yt.transcript(VID, "ru", fetch, use_ytdlp=False, post=post))
        self.assertEqual(cm.exception.code, "login")

    def test_long_videos_are_covered_whole_and_in_english(self) -> None:
        prompts = []

        async def complete(prompt):
            prompts.append(prompt)
            return "note"
        segs = [{"start": i * 5.0, "text": "word " * 40} for i in range(1000)]   # ~200 000 characters
        run(yt.summarize({"id": VID, "title": "t"}, segs, complete, mode="brief", lang="en"))
        parts = prompts[:-1]
        covered = sum(len(p) for p in parts)
        self.assertGreater(covered, 190_000)               # used to sample 12 of ~23 chunks: half the video unseen
        self.assertTrue(all(p.startswith("Video:") and "Part " in p for p in parts))
        self.assertNotIn("Часть", prompts[-1])
        self.assertIn("Below are notes", prompts[-1])

    def test_map_calls_run_in_parallel(self) -> None:
        active = peak = 0

        async def complete(_prompt):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return "n"
        segs = [{"start": i * 5.0, "text": "w " * 200} for i in range(400)]
        run(yt.summarize({"id": VID}, segs, complete, chunk_chars=5000, max_chunks=8))
        self.assertGreater(peak, 1)
        self.assertLessEqual(peak, yt.MAP_CONCURRENCY)

    def test_bad_url_is_rejected_before_the_model_is_needed(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        yt.register(app, lambda: None, fetch=lambda _u: None, use_ytdlp=False)
        client = TestClient(app)
        r = client.post("/api/youtube/analyze", json={"url": "https://example.com"})
        self.assertEqual((r.status_code, r.json()["code"]), (422, "bad_url"))

    def test_transcript_route_reports_unexpected_errors(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        async def boom(_url):
            raise RuntimeError("boom")

        async def post_boom(_url, _payload):
            raise ValueError("bad json")
        app = FastAPI()
        yt.register(app, lambda: None, fetch=boom, use_ytdlp=False, post=post_boom)
        r = TestClient(app).post("/api/youtube/transcript", json={"url": f"https://youtu.be/{VID}"})
        self.assertIn(r.status_code, (422, 502))
        self.assertIn("code", r.json())

    def test_frozen_build_does_not_spawn_itself_for_yt_dlp(self) -> None:
        started = []
        with patch.object(yt.shutil, "which", return_value=None), patch.object(sys, "frozen", True, create=True), \
                patch.object(yt.subprocess, "run", side_effect=lambda *a, **k: started.append(a)):
            self.assertEqual(run(yt._ytdlp(VID, "ru")), [])
        self.assertEqual(started, [])


if __name__ == "__main__":
    unittest.main()
