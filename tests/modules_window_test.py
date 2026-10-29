"""Playground routes the module window, the workbench and the bridge really call."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import pgdata
from app.playground_api import create_app

class ModuleRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.modules = self.root / "modules"
        self._patches = [
            patch.object(pgdata, "MEMU_DIR", self.data),
            patch("app.modules_host.default_root", return_value=self.modules),

            patch("pathlib.Path.home", return_value=self.root / "home"),
        ]
        for item in self._patches:
            item.start()

    def tearDown(self) -> None:
        for item in reversed(self._patches):
            item.stop()
        self._temp.cleanup()

    def client(self) -> TestClient:
        return TestClient(create_app(), base_url="http://127.0.0.1")

    def enable(self, client: TestClient, module_id: str) -> None:
        response = client.post("/api/pg/modules/configure",
                               json={"id": module_id, "enabled": True})
        self.assertEqual(response.status_code, 200, response.text)

    def test_document_is_frameable_and_a_disabled_module_is_refused(self) -> None:
        with self.client() as client:

            refused = client.get("/api/pg/modules/translator/document")
            self.assertEqual(refused.status_code, 403)
            self.assertIn("выключен", refused.json()["error"])

            self.enable(client, "translator")
            page = client.get("/api/pg/modules/translator/document")
            self.assertEqual(page.status_code, 200, page.text)
            self.assertIn("window.Memu", page.text)
            self.assertIn("Memu.stream", page.text)
            self.assertIn("--accent", page.text)
            self.assertEqual(page.headers["x-frame-options"].upper(), "SAMEORIGIN")
            self.assertEqual(page.headers["cache-control"], "no-store")
            policy = page.headers["content-security-policy"]
            self.assertIn("frame-ancestors 'self'", policy)
            self.assertNotIn("frame-ancestors 'none'", policy)
            self.assertIn("script-src 'unsafe-inline'", policy)

            self.assertIn("connect-src 'none'", policy)

            missing = client.get("/api/pg/modules/nope-nope/document")
            self.assertEqual(missing.status_code, 404)

    def test_every_other_playground_route_stays_unframeable(self) -> None:
        with self.client() as client:
            listing = client.get("/api/pg/modules")
            self.assertEqual(listing.status_code, 200)
            self.assertEqual(listing.headers["x-frame-options"].upper(), "DENY")
            self.assertIn("frame-ancestors 'none'", listing.headers["content-security-policy"])

    def test_listing_exposes_the_full_module_surface(self) -> None:
        with self.client() as client:
            rows = client.get("/api/pg/modules").json()["modules"]
            self.assertEqual([row["id"] for row in rows], ["translator", "studio", "design", "benchmarks", "catalog", "api-models", "characters", "music", "learn", "memudb"])
            for row in rows:
                self.assertTrue(row["builtin"])
                self.assertFalse(row["enabled"])
                for key in ("title", "title_en", "summary", "summary_en", "window",
                            "launcher", "persist", "permissions", "bridge", "version", "icon"):
                    self.assertIn(key, row, key)
                self.assertIn(row["launcher"]["slot"],
                              ("sidebar", "chat-list", "topbar", "composer"))

    def test_templates_and_routes_are_served(self) -> None:
        with self.client() as client:
            templates = client.get("/api/pg/modules/templates").json()
            self.assertGreaterEqual(len(templates["templates"]), 4)

            self.assertEqual(len(templates["permissions"]), 15)
            self.assertIn("background", templates["permissions"])
            self.assertEqual(templates["slots"],
                             ["sidebar", "chat-list", "topbar", "composer"])
            for item in templates["templates"]:
                self.assertTrue(item["title_ru"] and item["title_en"])
                self.assertTrue(item["summary_ru"] and item["summary_en"])

            routes = client.get("/api/pg/modules/routes").json()
            for key in ("chat", "image", "video", "audio", "transcribe", "download"):
                self.assertTrue(routes["routes"][key]["available"])

            self.assertEqual(client.get("/api/pg/modules/wiki").status_code, 404)

    def test_create_edit_export_import_delete_round_trip(self) -> None:
        with self.client() as client:
            created = client.post("/api/pg/modules", json={
                "id": "route-calc", "title": "Калькулятор", "template": "tool",
                "window": {"default_width": 520, "default_height": 420},
            })
            self.assertEqual(created.status_code, 200, created.text)
            row = created.json()["module"]
            self.assertEqual(row["id"], "route-calc")
            self.assertIn("a", row["persist"])

            source = client.get("/api/pg/modules/route-calc/source").json()
            self.assertGreater(len(source["html"]), 80)
            self.assertGreater(len(source["js"]), 80)
            self.assertFalse(source["builtin"])

            edited = client.post("/api/pg/modules/code", json={
                "id": "route-calc", "html": "<p id='o'>x</p>", "css": "p{}",
                "js": "document.querySelector('#o').textContent='7'",
                "persist": ["draft"], "permissions": ["storage"],
                "window": {"default_width": 500, "default_height": 400},
            })
            self.assertEqual(edited.status_code, 200, edited.text)
            self.assertEqual(edited.json()["module"]["persist"], ["draft"])
            self.assertEqual(edited.json()["module"]["permissions"], ["storage"])

            validation = client.post("/api/pg/modules/validate", json={
                "html": "<p>x</p></body>", "css": "", "js": "Memu.save({ zzz: 1 });",
                "persist": [],
            }).json()
            self.assertFalse(validation["ok"])
            self.assertTrue({item["field"] for item in validation["problems"]} >= {"html", "persist"})

            exported = client.get("/api/pg/modules/route-calc/export")
            self.assertEqual(exported.status_code, 200)
            payload = json.loads(exported.text)
            self.assertEqual(payload["format"], "memu-module")
            self.assertIn("memu-module.json", exported.headers["content-disposition"])

            imported = client.post("/api/pg/modules/import",
                                   json={"package": payload, "new_id": "route-calc-2",
                                         "title": "Калькулятор 2"})
            self.assertEqual(imported.status_code, 200, imported.text)
            self.assertEqual(imported.json()["module"]["title"], "Калькулятор 2")

            duplicated = client.post("/api/pg/modules/duplicate",
                                     json={"id": "route-calc-2", "new_id": "route-calc-3"})
            self.assertEqual(duplicated.status_code, 200, duplicated.text)

            for module_id in ("route-calc", "route-calc-2", "route-calc-3"):
                deleted = client.post("/api/pg/modules/delete", json={"id": module_id})
                self.assertEqual(deleted.status_code, 200, deleted.text)

            self.assertEqual(client.post("/api/pg/modules/delete",
                                         json={"id": "translator"}).status_code, 200)
            listing = client.get("/api/pg/modules").json()
            self.assertNotIn("translator", [row["id"] for row in listing["modules"]])
            self.assertEqual([row["id"] for row in listing["removed"]], ["translator"])
            self.assertEqual(client.get("/api/pg/modules/translator/document").status_code, 404)
            restored = client.post("/api/pg/modules/restore", json={"id": "translator"})
            self.assertEqual(restored.status_code, 200, restored.text)
            self.assertIn("translator", [row["id"] for row in client.get("/api/pg/modules").json()["modules"]])

    def test_persist_route_keeps_only_allowed_keys(self) -> None:
        with self.client() as client:
            self.enable(client, "translator")
            saved = client.post("/api/pg/modules/persist", json={
                "id": "translator", "fields": {"source": "привет", "evil": "no"},
            }).json()["saved"]
            self.assertEqual(saved["source"], "привет")
            self.assertNotIn("evil", saved)
            read_back = client.post("/api/pg/modules/persist", json={"id": "translator"}).json()
            self.assertEqual(read_back["saved"]["source"], "привет")
            cleared = client.post("/api/pg/modules/persist",
                                  json={"id": "translator", "clear": True}).json()
            self.assertEqual(cleared["saved"], {})

    def test_persist_needs_storage_permission_and_cannot_escape(self) -> None:
        """Writing saved fields needs `storage`; a `..` id must not reach disk."""
        with self.client() as client:
            client.post("/api/pg/modules", json={
                "id": "store-less", "title": "S", "js": "void 0",
                "persist": ["draft"], "permissions": ["chat"]})
            refused = client.post("/api/pg/modules/persist", json={
                "id": "store-less", "fields": {"draft": "WRITTEN"}})
            self.assertEqual(refused.status_code, 403, refused.text)
            self.assertIn("не разрешено", refused.json()["error"])

            self.assertEqual(client.post("/api/pg/modules/persist",
                                         json={"id": "store-less"}).status_code, 200)

            client.post("/api/pg/modules/code", json={
                "id": "store-less", "html": "<p>x</p>", "css": "", "js": "void 0",
                "persist": ["draft"], "permissions": ["storage"]})
            allowed = client.post("/api/pg/modules/persist", json={
                "id": "store-less", "fields": {"draft": "ok"}})
            self.assertEqual(allowed.status_code, 200, allowed.text)
            self.assertEqual(allowed.json()["saved"]["draft"], "ok")

            for bad in ("../../config", "../state", "..", "with space"):
                response = client.post("/api/pg/modules/persist",
                                       json={"id": bad, "fields": {"draft": "x"}})

                self.assertIn(response.status_code, (400, 403), (bad, response.text))
                self.assertNotEqual(response.status_code, 200, bad)

    def test_every_media_route_enforces_its_permission(self) -> None:
        """Media, files and notify are gated, on the route the bridge really calls."""
        with self.client() as client:
            created = client.post("/api/pg/modules", json={
                "id": "no-media", "title": "N", "js": "void 0", "persist": [],
                "permissions": ["storage"]})
            self.assertEqual(created.status_code, 200, created.text)
            self.assertEqual(created.json()["module"]["permissions"], ["storage"])
            probes = (
                ("/api/pg/modules/media", {"action": "image", "model": "m", "prompt": "x"}),
                ("/api/pg/modules/media", {"action": "video", "model": "m", "prompt": "x"}),
                ("/api/pg/modules/speak", {"text": "x"}),
                ("/api/pg/modules/transcribe", {"audio": "x"}),
                ("/api/pg/modules/extract", {"name": "a.txt",
                                             "data": "data:text/plain;base64,aGk="}),
                ("/api/pg/modules/notify", {"title": "t", "text": "b"}),
            )
            for path, extra in probes:
                response = client.post(path, json={"module_id": "no-media", **extra})
                self.assertEqual(response.status_code, 403, (path, response.text))

            loaded = client.get("/api/pg/modules/media-file",
                                params={"module_id": "no-media", "url": "https://x/y.png"})
            self.assertEqual(loaded.status_code, 403, loaded.text)

            client.post("/api/pg/modules/code", json={
                "id": "no-media", "html": "<p>x</p>", "css": "", "js": "void 0",
                "persist": [], "permissions": ["media", "files"]})
            image = client.post("/api/pg/modules/media", json={
                "module_id": "no-media", "action": "image", "model": "m", "prompt": "x"})
            self.assertEqual(image.status_code, 200, image.text)
            empty = client.post("/api/pg/modules/media", json={
                "module_id": "no-media", "action": "image", "model": "m", "prompt": "  "})
            self.assertEqual(empty.status_code, 400, empty.text)

    def test_window_route_remembers_geometry(self) -> None:
        with self.client() as client:
            policy = client.post("/api/pg/modules/window",
                                 json={"id": "translator"}).json()["window"]
            self.assertGreater(policy["width"], 200)
            self.assertEqual(policy["size_source"], "authored")
            saved = client.post("/api/pg/modules/window", json={
                "id": "translator", "box": {"width": 700, "height": 500, "x": 30, "y": 40},
            }).json()["window"]
            self.assertEqual((saved["width"], saved["height"]), (700, 500))
            self.assertEqual(saved["size_source"], "user")
            again = client.post("/api/pg/modules/window",
                                json={"id": "translator"}).json()["window"]
            self.assertEqual((again["width"], again["height"]), (700, 500))

    def test_preview_route_renders_unsaved_code(self) -> None:
        with self.client() as client:
            response = client.post("/api/pg/modules/preview", json={
                "id": "preview-demo", "title": "Preview", "html": "<p id='p'>hi</p>",
                "css": "#p{color:red}", "js": "void 0", "persist": [],
                "permissions": ["storage"], "env": {"theme": "light", "lang": "en"},
            })
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("#p{color:red}", response.text)
            self.assertIn("window.Memu", response.text)
            self.assertIn('data-theme="light"', response.text)
            self.assertEqual(response.headers["x-frame-options"].upper(), "SAMEORIGIN")
            self.assertEqual(response.headers["cache-control"], "no-store")

    def test_request_bridge_needs_a_model_and_a_permission(self) -> None:
        with self.client() as client:
            self.enable(client, "translator")
            missing = client.post("/api/pg/modules/request", json={
                "module_id": "translator", "execute": True,
                "model": "", "instructions": "rules", "text": "hello",
            })
            self.assertEqual(missing.status_code, 400)
            self.assertIn("модел", missing.json()["error"].lower())
            self.assertNotIn("text", missing.json())

            preview = client.post("/api/pg/modules/request", json={
                "module_id": "translator", "execute": False,
                "model": "", "instructions": "RULE", "text": "hello",
            })
            self.assertEqual(preview.status_code, 200)
            self.assertEqual(preview.json()["instructions"], "RULE")

            client.post("/api/pg/modules/code", json={
                "id": "translator", "html": "<p>x</p>", "css": "", "js": "void 0",
                "persist": [], "permissions": ["storage"],
            })
            denied = client.post("/api/pg/modules/request", json={
                "module_id": "translator", "model": "genspark-x", "text": "hi",
            })
            self.assertEqual(denied.status_code, 403)
            self.assertIn("не разрешено", denied.json()["error"])
            stream = client.post("/api/pg/modules/stream", json={
                "module_id": "translator", "model": "genspark-x", "text": "hi",
            })
            self.assertEqual(stream.status_code, 403)

    def test_handoff_and_download_need_the_download_permission(self) -> None:
        with self.client() as client:
            self.enable(client, "translator")
            client.post("/api/pg/modules/code", json={
                "id": "translator", "html": "<p>x</p>", "css": "", "js": "void 0",
                "persist": [], "permissions": ["chat"],
            })
            refused = client.post("/api/pg/modules/handoff",
                                  json={"module_id": "translator", "code": "<p>x</p>"})
            self.assertEqual(refused.status_code, 403)
            refused_download = client.post("/api/pg/modules/download",
                                           json={"module_id": "translator", "name": "a.txt",
                                                 "text": "x"})
            self.assertEqual(refused_download.status_code, 403)

            client.post("/api/pg/modules/code", json={
                "id": "translator", "html": "<p>x</p>", "css": "", "js": "void 0",
                "persist": [], "permissions": ["download"],
            })
            bundle = client.post("/api/pg/modules/handoff", json={
                "module_id": "translator", "prompt": "Дизайн",
                "images": ["data:image/png;base64,aaa"], "code": "<h1>x</h1>",
            })
            self.assertEqual(bundle.status_code, 200, bundle.text)
            data = bundle.json()
            self.assertEqual(data["prompt"], "Дизайн")
            self.assertEqual(len(data["images"]), 1)
            self.assertIn("другой ИИ", data["readme"])
            self.assertEqual([item["name"] for item in data["files"]], ["index.html"])

    def test_models_and_chat_routes_degrade_cleanly(self) -> None:
        with self.client() as client:

            models = client.post("/api/pg/modules/models")
            self.assertIn(models.status_code, (200, 502))
            chat = client.get("/api/pg/modules/translator/chat").json()
            self.assertEqual(chat["messages"], [])
            self.assertEqual(chat["id"], "module-translator")

    def test_passing_a_permission_where_an_action_belongs_is_reported(self) -> None:
        """`files`/`media`/`storage` are permissions: as actions they allow all.

        This mistake silently disabled the permission check once, so it raises
        instead. Names that are both an action and a permission (`download`,
        `notify`, `clipboard`) must keep working.
        """
        from app.modules_host import ACTION_PERMISSION, PERMISSIONS
        both = {name for name in PERMISSIONS if name in ACTION_PERMISSION}
        self.assertTrue(both, "ожидались имена, которые и действие, и право")
        with self.client() as client:
            for action in sorted(both):
                response = client.post("/api/pg/modules/configure",
                                       json={"id": "translator", "enabled": True})
                self.assertEqual(response.status_code, 200)

                probe = client.post("/api/pg/modules/download",
                                    json={"module_id": "translator", "name": "x.txt",
                                          "text": "x"})
                self.assertIn(probe.status_code, (200, 403), (action, probe.text))

    def test_configure_reorder_and_reset_validate_input(self) -> None:
        with self.client() as client:
            self.assertEqual(client.post("/api/pg/modules/configure",
                                         json={"id": "missing"}).status_code, 404)
            self.assertEqual(client.post("/api/pg/modules/configure",
                                         json={"id": "translator",
                                               "slot": "nowhere"}).status_code, 400)
            ordered = client.post("/api/pg/modules/reorder",
                                  json={"order": ["design", "studio", "translator"]})
            self.assertEqual(ordered.status_code, 200)
            self.assertEqual([row["id"] for row in ordered.json()["modules"]],
                         ["design", "studio", "translator", "benchmarks", "catalog", "api-models", "characters", "music", "learn", "memudb"])
            self.assertEqual(client.post("/api/pg/modules/reset",
                                         json={"id": "translator"}).status_code, 200)
            self.assertEqual(client.post("/api/pg/modules/reset",
                                         json={"id": "route-none"}).status_code, 400)
            self.assertEqual(client.post("/api/pg/modules", json={"id": "BAD ID"}).status_code, 400)

if __name__ == "__main__":
    unittest.main()
