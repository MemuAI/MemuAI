# -*- coding: utf-8 -*-
"""Round 73, items 9-10: web search in the «Characters» module.

Tests 1-4 pin the SHARED part of the chain (app/characters_api.py,
app/pgstatic/modules.js, app/modules_host.py). They FAIL on the current tree
on purpose: those files are outside this round's scope and the coordinator
must apply the patch quoted in the round report first. Everything the module
itself owns (tests 5-7) passes now, and the browser behaviour with the patch
applied in-memory is proven by ~/workspace/verify73/characters/.

The chain, end to end:
  characters buildFor -> params.web_search
  -> Memu.complete -> bridge (modules_host BRIDGE_JS, onWebSearch)
  -> host runStream (pgstatic/modules.js) -> POST /api/pg/modules/complete
  -> characters_api.build_completion -> Proxy /api/admin/playground/chat
  -> app/chat.py (web_search event) -> modules_complete forwards the event
  -> bridge -> module onWebSearch -> sources block on the message.
"""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import characters_api as api
from app import modules_host

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "app" / "module_builtins" / "characters"


class BuildCompletionWebSearchTest(unittest.TestCase):
    def test_web_search_comes_from_params(self):
        base = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
        self.assertTrue(api.build_completion({**base, "params": {"web_search": True}})["web_search"])
        self.assertFalse(api.build_completion({**base, "params": {"web_search": False}})["web_search"])
        self.assertFalse(api.build_completion(base)["web_search"])


class CompleteStreamWebSearchTest(unittest.TestCase):
    def test_memuai_event_is_forwarded(self):
        frames = [
            'data: {"object": "memuai.event", "event": "web_search", '
            '"web_search": {"query": "q", "sources": [{"title": "T", "url": "https://example.com"}]}}',
            'data: {"choices": [{"delta": {"content": "Hi"}}]}',
            "data: [DONE]",
        ]
        seen = {}

        async def fake_lines(path, payload):
            seen["path"] = path
            seen["body"] = json.loads(payload)
            for line in frames:
                yield line

        with tempfile.TemporaryDirectory() as temp:
            from app import characters_api
            local = FastAPI()
            with patch("app.modules_host.default_root", return_value=Path(temp)):
                characters_api.register(local, stream_lines=fake_lines)
                client = TestClient(local)
                r = client.post("/api/pg/modules/complete", json={
                    "module_id": "characters", "model": "m",
                    "messages": [{"role": "user", "content": "hi"}],
                    "params": {"web_search": True}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(seen["body"]["web_search"], "params.web_search must reach the Proxy")
        events = [json.loads(line[5:]) for line in r.text.splitlines() if line.startswith("data:")]
        web = [e for e in events if e.get("event") == "web_search"]
        self.assertEqual(len(web), 1, events)
        self.assertEqual(web[0]["web_search"]["query"], "q")
        self.assertEqual(web[0]["web_search"]["sources"][0]["url"], "https://example.com")


class SharedBridgeContractTest(unittest.TestCase):
    def test_bridge_registers_on_web_search(self):
        source = (ROOT / "app" / "modules_host.py").read_text(encoding="utf-8")
        self.assertIn("onWebSearch: options.onWebSearch", source)
        self.assertIn('data.event === "web_search"', source)

    def test_host_run_stream_forwards_web_search(self):
        source = (ROOT / "app" / "pgstatic" / "modules.js").read_text(encoding="utf-8")
        self.assertIn('item.event === "web_search"', source)
        self.assertIn('send({ event: "web_search"', source)


class DefaultAvatarSvgTest(unittest.TestCase):
    def test_square_canvas_centred_letter(self):
        source = (MODULE / "main.js").read_text(encoding="utf-8")
        fn = re.search(r"function defaultAvatar\(name, seed\) \{.*?\n\}", source, re.S).group(0)
        self.assertIn('width="200" height="200"', fn)
        self.assertIn('viewBox="0 0 200 200"', fn)
        self.assertNotIn('height="300"', fn, "no portrait canvas: it crops the glyph in circles")
        self.assertNotIn('viewBox="0 0 200 300"', fn)
        self.assertIn('y="100"', fn)
        self.assertIn('dominant-baseline="central"', fn)


class WebSearchI18nTest(unittest.TestCase):
    def test_keys_exist_in_both_languages(self):
        data = json.loads((MODULE / "i18n.json").read_text(encoding="utf-8"))
        keys = ("web_search", "web_search_hint", "web_search_sources", "web_search_open")
        for lang in ("ru", "en"):
            for key in keys:
                self.assertTrue(data[lang][key].strip(), (lang, key))
        self.assertEqual(set(data["ru"]), set(data["en"]))


class ModuleWiringTest(unittest.TestCase):
    def test_toggle_params_and_sources_rendering(self):
        source = (MODULE / "main.js").read_text(encoding="utf-8")
        self.assertIn('id: "web-search-chip"', source)
        self.assertIn('"aria-pressed"', source)
        self.assertIn("function chatWebSearch()", source)
        self.assertIn("{ web_search: chatWebSearch() }", source)
        self.assertIn("onWebSearch: function (data)", source)
        self.assertIn("function searchSourcesNode(msg)", source)
        self.assertIn("normalizeSearchUsed", source)
        css = (MODULE / "style.css").read_text(encoding="utf-8")
        self.assertIn(".ch-src-block", css)
        self.assertIn(".ch-search-chip.on", css)


if __name__ == "__main__":
    unittest.main()
