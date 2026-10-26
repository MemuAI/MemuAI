# -*- coding: utf-8 -*-
"""Round 29: generic module bridge extensions (app/characters_api.py): Memu.complete, Memu.store, Memu.asset."""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import characters_api as api
from app import modules_host, pgdata

class StoreHelpersTest(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name)

    def tearDown(self):
        self._t.cleanup()

    def test_keys_are_validated(self):
        for bad in ("", "../x", "a/../b", "a//b", "/abs", "a\\b", "con", "a/nul.txt", "x/y/z/w", "a.", ".hidden", "ключ"):
            with self.assertRaises(ValueError, msg=bad):
                api.check_key(bad)
        for good in ("settings", "chats/c1/chat-2", "presets/pr_x.1"):
            self.assertEqual(api.check_key(good), good)

    def test_round_trip_list_remove(self):
        api.store_set(self.root, "characters", "chats/c1/a", {"messages": [1, 2]})
        api.store_set(self.root, "characters", "settings", {"x": 1})
        self.assertEqual(api.store_get(self.root, "characters", "chats/c1/a"), {"messages": [1, 2]})
        self.assertEqual([r["key"] for r in api.store_list(self.root, "characters", "chats/")], ["chats/c1/a"])
        api.store_remove(self.root, "characters", "chats/c1/a")
        self.assertIsNone(api.store_get(self.root, "characters", "chats/c1/a"))
        self.assertFalse((self.root / "store" / "characters" / "chats").exists(), "empty namespaces are removed")

        self.assertIsNone(api.store_get(self.root, "translator", "settings"))

    def test_value_limit(self):
        with patch.object(api, "STORE_VALUE_LIMIT", 100):
            with self.assertRaises(ValueError):
                api.store_set(self.root, "characters", "big", "x" * 500)

    def test_build_completion_validates_and_passes_samplers(self):
        body = api.build_completion({"model": "m", "messages": [
            {"role": "system", "content": "s"}, {"role": "assistant", "content": "a", "name": "Alice"},
            {"role": "user", "content": [{"type": "text", "text": "u"}, {"type": "image_url", "image_url": {"url": "https://evil/x.png"}},
                                         {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]},
            {"role": "tool", "content": "t"}, {"role": "user", "content": ""}],
            "params": {"temperature": 9, "top_p": 0.9, "top_k": 0, "min_p": 0.05, "frequency_penalty": 0, "presence_penalty": 0.4,
                       "repetition_penalty": 1, "seed": 7, "stop": ["\nUser:", ""], "effort": "high", "max_tokens": "300", "thinking": True}})
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "assistant", "user", "user"])
        self.assertEqual(body["messages"][1]["name"], "Alice")
        self.assertEqual(len(body["messages"][2]["content"]), 2, "remote image URLs are dropped")
        self.assertEqual(body["temperature"], 2.0)
        self.assertNotIn("top_k", body)
        self.assertNotIn("frequency_penalty", body)
        self.assertNotIn("repetition_penalty", body)
        self.assertEqual(body["min_p"], 0.05)
        self.assertEqual(body["presence_penalty"], 0.4)
        self.assertEqual(body["seed"], 7)
        self.assertEqual(body["stop"], ["\nUser:"])
        self.assertEqual(body["effort"], "high")
        self.assertEqual(body["max_tokens"], 300)
        self.assertTrue(body["thinking"])
        self.assertFalse(body["web_search"])
        with self.assertRaises(ValueError):
            api.build_completion({"model": "", "messages": [{"role": "user", "content": "x"}]})
        with self.assertRaises(ValueError):
            api.build_completion({"model": "m", "messages": []})

    def test_asset_reading_is_confined(self):
        with patch("app.modules_host.default_root", return_value=self.root):
            index = api.read_asset("characters", "presets/index.json")
            self.assertEqual(index["json"]["default"], "universal")
            for bad in ("../manifest.json", "presets/../../main.js", "/etc/passwd", "presets//x.json"):
                with self.assertRaises((ValueError, KeyError), msg=bad):
                    api.read_asset("characters", bad)
            with self.assertRaises(KeyError):
                api.read_asset("characters", "missing.json")

class SearchProviderTest(unittest.TestCase):
    def test_characters_reach_the_global_search(self):
        from app import search_index
        with tempfile.TemporaryDirectory() as temp, patch("app.modules_host.default_root", return_value=Path(temp)):
            api.store_set(Path(temp), "characters", "index",
                          [{"id": "c1", "name": "Seraphina", "tags": ["forest"], "summary": "Guardian of the glade", "updated": 5}])
            self.assertTrue(api.register_search())
            try:
                found = search_index.search("glade", types=["character"])
                self.assertEqual([item["id"] for item in found["items"]], ["character:c1"])
                self.assertEqual(found["items"][0]["open"], {"action": "character", "id": "c1", "module_id": "characters"})
            finally:
                search_index.unregister_provider("characters")

class RoutesTest(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        root = Path(self._t.name)
        (root / "data").mkdir()
        self.modules = root / "modules"
        self._p = [patch.object(pgdata, "MEMU_DIR", root / "data"),
                   patch("app.modules_host.default_root", return_value=self.modules)]
        for p in self._p:
            p.start()
        from app.playground_api import create_app
        self.client = TestClient(create_app(), base_url="http://127.0.0.1")

    def tearDown(self):
        for p in reversed(self._p):
            p.stop()
        self._t.cleanup()

    def test_store_and_asset_routes(self):
        c = self.client
        r = c.post("/api/pg/modules/store", json={"module_id": "characters", "op": "set", "key": "a/b", "value": {"x": 1}})
        self.assertEqual(r.status_code, 200, r.text)
        r = c.post("/api/pg/modules/store", json={"module_id": "characters", "op": "get", "key": "a/b"})
        self.assertEqual(r.json()["value"], {"x": 1})
        r = c.post("/api/pg/modules/store", json={"module_id": "characters", "op": "get", "key": "../../config"})
        self.assertEqual(r.status_code, 400)
        r = c.post("/api/pg/modules/store", json={"module_id": "characters", "op": "usage"})
        self.assertEqual(r.json()["keys"], 1)

        bench = modules_host.load_package(self.modules, "benchmarks")
        if "storage" not in bench["permissions"]:
            r = c.post("/api/pg/modules/store", json={"module_id": "benchmarks", "op": "get", "key": "x"})
            self.assertEqual(r.status_code, 403)
        r = c.post("/api/pg/modules/asset", json={"module_id": "characters", "name": "presets/index.json"})
        self.assertEqual(r.status_code, 200)
        r = c.post("/api/pg/modules/asset", json={"module_id": "characters", "name": "../manifest.json"})
        self.assertEqual(r.status_code, 400)

    def test_complete_streams_text_reasoning_usage(self):
        frames = [
            'data: {"choices":[{"delta":{"reasoning_content":"hmm"}}]}',
            'data: {"choices":[{"delta":{"content":"Hel"}}]}',
            'data: {"choices":[{"delta":{"content":"lo"},"finish_reason":"stop"}]}',
            'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":2}}',
            "data: [DONE]",
        ]
        seen = {}

        async def fake_lines(path, payload):
            seen["path"] = path
            seen["body"] = json.loads(payload)
            for line in frames:
                yield line

        app = self.client.app

        from app import characters_api
        from fastapi import FastAPI
        local = FastAPI()
        with patch("app.modules_host.default_root", return_value=self.modules):
            characters_api.register(local, stream_lines=fake_lines)
            client = TestClient(local)
            r = client.post("/api/pg/modules/complete", json={"module_id": "characters", "model": "m",
                                                               "messages": [{"role": "user", "content": "hi"}],
                                                               "params": {"temperature": 0.5}})
        self.assertEqual(r.status_code, 200)
        events = [json.loads(line[5:]) for line in r.text.splitlines() if line.startswith("data:")]
        kinds = [e.get("event") or e.get("status") for e in events]
        self.assertIn("reasoning", kinds)
        self.assertEqual("".join(e["delta"] for e in events if e.get("event") == "delta"), "Hello")
        done = events[-1]
        self.assertEqual(done["event"], "done")
        self.assertEqual(done["result"]["text"], "Hello")
        self.assertEqual(done["result"]["reasoning"], "hmm")
        self.assertEqual(done["result"]["usage"]["prompt_tokens"], 10)
        self.assertEqual(seen["path"], "/api/admin/playground/chat")
        self.assertEqual(seen["body"]["temperature"], 0.5)
        _ = app

    def test_bridge_tables_match(self):
        source = (Path(__file__).resolve().parents[1] / "app" / "pgstatic" / "modules.js").read_text(encoding="utf-8")
        block = re.search(r"const ACTION_PERMISSION = \{(.*?)\};", source, re.S).group(1)
        table = dict(re.findall(r"(\w+):\s*\"(\w+)\"", block))
        self.assertEqual(table.get("complete"), "chat")
        self.assertEqual(table.get("store"), "storage")
        self.assertIn("asset", modules_host.FREE_ACTIONS)
        page = modules_host.module_document({"id": "x", "html": "<b></b>", "js": ""})
        for api_name in ("complete:", "store:", "asset:", "onReasoning", "onUsage"):
            self.assertIn(api_name, page)

if __name__ == "__main__":
    unittest.main()
