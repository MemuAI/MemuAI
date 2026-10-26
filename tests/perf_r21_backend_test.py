"""Round 21 performance: backend and MemuCode start-up changes keep their behaviour.

* ``model_info`` caches its pure name functions; ``lookup_known`` still hands out a fresh dict.
* Proxy ``/api/admin/pg/state`` is serialised directly (``JSONResponse``) — same JSON as before.
* MemuCode's interactive import path no longer imports httpx; ``llm.httpx`` loads it on first use.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

class ModelInfoCacheTest(unittest.TestCase):
    def test_lookup_known_returns_a_private_copy(self):
        from app import model_info
        first = model_info.lookup_known("anthropic/claude-opus-4-5-20251101")
        self.assertIsNotNone(first)
        first["context"] = -1
        first["label"] = "mutated"
        second = model_info.lookup_known("anthropic/claude-opus-4-5-20251101")
        self.assertNotEqual(second["context"], -1)
        self.assertNotEqual(second["label"], "mutated")

    def test_cached_functions_accept_any_value_like_before(self):
        from app import model_info
        self.assertEqual(model_info.canonical(None), "")
        self.assertEqual(model_info.canonical(""), "")
        self.assertIsNone(model_info.vision_hint(None))
        self.assertIsNone(model_info.lookup_known(None))

        self.assertIsInstance(model_info.canonical(["x"]), str)
        self.assertEqual(model_info.canonical("Claude-Opus-5-5:thinking"),
                         model_info.canonical("claude-opus-5-5:thinking"))

    def test_resolve_is_stable_across_calls(self):
        from app import model_info
        a = model_info.resolve("x", "custom", "gpt-5.1")
        b = model_info.resolve("x", "custom", "gpt-5.1")
        self.assertEqual(a, b)
        a["context"] = 1
        self.assertNotEqual(model_info.resolve("x", "custom", "gpt-5.1").get("context"), 1)

class PgStateResponseTest(unittest.TestCase):
    def test_state_is_plain_json(self):
        from fastapi import FastAPI
        from fastapi.encoders import jsonable_encoder
        from fastapi.testclient import TestClient
        from app.admin_api import router
        app = FastAPI()
        app.include_router(router)
        with TestClient(app, base_url="http://127.0.0.1") as client:
            response = client.get("/api/admin/pg/state")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.headers["content-type"].startswith("application/json"))
        data = response.json()
        self.assertEqual(set(data), {"models", "image_models", "video_models", "music_models", "memory"})
        self.assertEqual(jsonable_encoder(data), data)

        self.assertEqual(response.content,
                         json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"))

class MemuCodeLazyHttpxTest(unittest.TestCase):
    def test_interactive_imports_do_not_load_httpx(self):
        code = ("import sys, app.cli.main, app.cli.runtime, app.cli.tui.app, app.cli.tui.banner; "
                "print('httpx' in sys.modules)")
        out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True,
                             timeout=120, env={**os.environ, "PYTHONUTF8": "1"})
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        self.assertEqual(out.stdout.strip().splitlines()[-1], "False")

    def test_lazy_module_resolves_and_replaces_itself(self):
        from app.cli import llm
        namespace = {}
        lazy = llm.LazyModule("json", namespace)
        self.assertEqual(lazy.dumps([1]), "[1]")
        self.assertIs(namespace["json"], json)

        self.assertTrue(issubclass(llm.httpx.TransportError, Exception))
        client = llm.http_client(5, url="http://127.0.0.1:1")
        import httpx
        self.assertIsInstance(client, httpx.AsyncClient)
        self.assertIs(llm.httpx, httpx)

if __name__ == "__main__":
    unittest.main()
