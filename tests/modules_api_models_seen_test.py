"""Round 21: «Новые модели API» must not replay notifications after a restart.

Root cause: the "already shown" cursor of module notifications lived in the
page's localStorage, and the desktop window (pywebview → WebView2 in its default
private mode) loses localStorage on every launch. The cursor fell back to 0 and
the last stored events were toasted as new after each start. The acknowledgement
now lives in background.json (the same profile store as the events), the module's
own "seen" mark too, and the per-source seen-set always keeps every current id.
"""
from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from app import module_monitor, modules_host

ROOT = Path(__file__).resolve().parents[1]

SPEC = {"id": "openrouter", "title": "OpenRouter", "url": "https://openrouter.ai/api/v1/models",
        "format": "json", "items_path": "data", "id_keys": ["id"], "label_keys": ["name", "id"],
        "families": [], "api_key": ""}

def record(model_id):
    return {"id": model_id, "name": model_id.upper(), "values": {}, "family": model_id.split("/")[0]}

class SeenStateTest(unittest.TestCase):
    def setUp(self):
        self.root = modules_host.default_root()
        self.root.mkdir(parents=True, exist_ok=True)
        modules_host._mutate_state(self.root, "api-models", lambda row: row.update(enabled=True))
        path = module_monitor._path()
        if path.exists():
            path.unlink()
        self.records = {"a/x": record("a/x")}

        async def fake_read(_source):
            return dict(self.records)

        patcher = patch.object(module_monitor, "read_source", fake_read)
        patcher.start()
        self.addCleanup(patcher.stop)
        prefs = patch("app.pgdata.load_preferences", return_value={"notify_windows": False})
        prefs.start()
        self.addCleanup(prefs.stop)

    def configure(self, **changes):
        module_monitor.configure("api-models", {"enabled": True, "interval": 300, "mode": "new",
                                                "telegram": False, "sources": [{**SPEC, **changes}]})

    def scan(self):
        asyncio.run(module_monitor.scan("api-models", force=True))

    def restart(self):
        """Nothing survives a restart except files: drop every in-memory hint."""
        return json.loads(module_monitor._path().read_text(encoding="utf-8"))

    def test_shown_notifications_are_not_replayed_after_restart(self):
        self.configure()
        self.scan()
        self.assertEqual(module_monitor.pending_events()["events"], [])
        self.records["b/new"] = record("b/new")
        self.scan()
        pending = module_monitor.pending_events()
        self.assertEqual(len(pending["events"]), 1)
        self.assertIn("b/new", pending["events"][0]["text"])
        module_monitor.acknowledge(pending["cursor"])

        stored = self.restart()
        self.assertEqual(stored["ack"], pending["cursor"])
        self.assertEqual(module_monitor.pending_events()["events"], [], "replayed after restart")

        self.configure()
        self.scan()
        self.assertEqual(module_monitor.pending_events()["events"], [])
        self.assertEqual(len(module_monitor.events(0, "api-models")["events"]), 1)

    def test_upgrade_from_a_store_without_ack_does_not_replay_old_events(self):
        path = module_monitor._path()
        path.write_text(json.dumps({"jobs": {}, "sequence": 3, "events": [
            {"id": n, "module_id": "api-models", "title": "t", "text": f"+ m{n}", "at": 1} for n in (1, 2, 3)]}),
            encoding="utf-8")
        self.assertEqual(module_monitor.pending_events()["events"], [])
        self.assertEqual(self.restart()["ack"], 3)

    def test_unacknowledged_events_from_the_background_are_shown_once(self):
        self.configure()
        self.scan()
        module_monitor.pending_events()
        self.records["c/z"] = record("c/z")
        self.scan()
        first = module_monitor.pending_events()
        self.assertEqual(len(first["events"]), 1)
        module_monitor.acknowledge(first["cursor"])
        self.assertEqual(module_monitor.pending_events()["events"], [])
        module_monitor.acknowledge(0)
        self.assertEqual(module_monitor.pending_events()["events"], [])

    def test_module_seen_mark_persists_and_acknowledges(self):
        self.configure()
        self.scan()
        self.records["d/w"] = record("d/w")
        self.scan()
        cursor = module_monitor.events(0, "api-models")["cursor"]
        self.assertEqual(module_monitor.events(0, "api-models")["seen"], 0)
        module_monitor.mark_seen("api-models", cursor)
        stored = self.restart()
        self.assertEqual(stored["seen_events"]["api-models"], cursor)
        self.assertEqual(module_monitor.events(0, "api-models")["seen"], cursor)
        self.assertEqual(module_monitor.pending_events()["events"], [])
        module_monitor.mark_seen("api-models", cursor + 50)
        self.assertEqual(module_monitor.events(0, "api-models")["seen"], cursor)

    def test_seen_set_keeps_every_current_id_on_large_sources(self):
        with patch.object(module_monitor, "_SEEN_LIMIT", 5):
            current = {f"z/{n}": record(f"z/{n}") for n in range(4)} | {"a/first": record("a/first")}
            seen = module_monitor._remember_seen({"0/old-a", "0/old-b", "y/gone"}, current)
            self.assertTrue(set(current) <= set(seen))
            self.assertLessEqual(len(seen), 5)

    def test_http_routes_store_the_cursor_server_side(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.module_services_api import router
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        self.configure()
        self.scan()
        self.records["e/v"] = record("e/v")
        self.scan()
        first = client.get("/api/pg/modules/events?pending=1").json()
        self.assertEqual(len(first["events"]), 1)
        self.assertEqual(client.post("/api/pg/modules/events/ack", json={"cursor": first["cursor"]}).json()["acknowledged"],
                         first["cursor"])

        self.assertEqual(TestClient(app).get("/api/pg/modules/events?pending=1").json()["events"], [])

        self.assertEqual(len(client.get("/api/pg/modules/events?after=0").json()["events"]), 1)

    def test_page_no_longer_keeps_the_cursor_in_local_storage(self):
        source = (ROOT / "app" / "pgstatic" / "modules.js").read_text(encoding="utf-8")
        self.assertNotIn("memu_module_events_cursor", source)
        self.assertIn("/api/pg/modules/events?pending=1", source)
        self.assertIn("/api/pg/modules/events/ack", source)

if __name__ == "__main__":
    unittest.main()
