"""Security regressions an adversarial audit found in the module system.

Each check reproduces a real defect that was once reachable through the HTTP
API, so it belongs in the automatic suite rather than in a script somebody has
to remember to run. Every one of these passed against the vulnerable code and
must keep failing against a regression that brings the hole back.
"""
from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.playground_api import create_app

TOKEN_CONFIG = '{"providers":{"genspark":"token-abc"},"port":8317}'


class ModuleSecurityTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        base = Path(self._temp.name)
        self.root = base / "modules"
        self.root.mkdir()
        # Файл рядом с корнем модулей: до него не должен дотянуться модуль.
        self.victim = base / "config.json"
        self.victim.write_text(TOKEN_CONFIG, encoding="utf-8")
        self._patch = patch("app.modules_host.default_root", return_value=self.root)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self._temp.cleanup()

    def client(self) -> TestClient:
        return TestClient(create_app(), base_url="http://127.0.0.1")

    def test_persist_cannot_escape_the_module_root(self) -> None:
        """`../../config` once blanked the app config holding provider tokens."""
        with self.client() as client:
            for bad in ("../../config", "..", "../config", "../../../config",
                        "..%2F..%2Fconfig", "with space", "UPPER"):
                response = client.post("/api/pg/modules/persist",
                                       json={"id": bad, "fields": {"x": 1}})
                self.assertNotEqual(response.status_code, 200, (bad, response.text))
            self.assertEqual(self.victim.read_text(encoding="utf-8"), TOKEN_CONFIG)

    def test_every_disk_layer_refuses_a_hostile_id(self) -> None:
        """The guard is not one function: each layer that touches disk checks.

        Weakening a single layer must not open the hole, so every function that
        builds a path from an id is checked on its own.
        """
        from app import modules_host as host
        hostile = ("../../config", "..", "../config", "a/../b", "UPPER", "with space")
        for bad in hostile:
            with self.subTest(module_id=bad):
                with self.assertRaises(ValueError, msg=f"_safe_id accepted {bad!r}"):
                    host._safe_id(bad)
                for name, call in (
                    ("_package_dir", lambda: host._package_dir(self.root, bad)),
                    ("_persist_keys", lambda: host._persist_keys(self.root, bad)),
                    ("load_persistent", lambda: host.load_persistent(self.root, bad)),
                    ("save_persistent", lambda: host.save_persistent(self.root, bad, {"x": 1})),
                    ("clear_persistent", lambda: host.clear_persistent(self.root, bad)),
                    ("resolve_window", lambda: host.resolve_window(self.root, bad)),
                    ("remember_window", lambda: host.remember_window(
                        self.root, bad, {"width": 10, "height": 10})),
                    ("configure_module", lambda: host.configure_module(self.root, bad)),
                    ("duplicate_module", lambda: host.duplicate_module(self.root, bad, "copy")),
                    ("reset_override", lambda: host.reset_override(self.root, bad)),
                    ("export_package", lambda: host.export_package(self.root, bad)),
                    ("mark_open", lambda: host.mark_open(self.root, bad, True)),
                ):
                    with self.assertRaises((ValueError, KeyError),
                                           msg=f"{name} accepted {bad!r}"):
                        call()
        self.assertEqual(self.victim.read_text(encoding="utf-8"), TOKEN_CONFIG)

    def test_window_and_configure_cannot_escape_the_module_root(self) -> None:
        with self.client() as client:
            for bad in ("../../config", "..", "../victim"):
                for path in ("/api/pg/modules/window", "/api/pg/modules/configure",
                             "/api/pg/modules/code", "/api/pg/modules/delete",
                             "/api/pg/modules/reset", "/api/pg/modules/duplicate"):
                    body = {"id": bad, "box": {"width": 10, "height": 10},
                            "enabled": True, "html": "", "css": "", "js": "",
                            "permissions": []}
                    response = client.post(path, json=body)
                    self.assertIn(response.status_code, (400, 403, 404),
                                  (path, bad, response.text))
            self.assertEqual(self.victim.read_text(encoding="utf-8"), TOKEN_CONFIG)
            self.assertFalse((self.root.parent / "state.json").exists() and
                             (self.root.parent / "state.json").read_text(
                                 encoding="utf-8").strip() == "{}")

    def test_media_and_file_actions_need_their_permission(self) -> None:
        """Every bridge action must be gated on the route the bridge really calls."""
        sample = "data:text/plain;base64," + base64.b64encode(b"hi").decode()
        with self.client() as client:
            created = client.post("/api/pg/modules", json={
                "id": "gated", "title": "G", "js": "void 0", "persist": [],
                "permissions": ["storage"]})
            self.assertEqual(created.status_code, 200, created.text)
            probes = (
                ("/api/pg/modules/media", {"action": "image", "model": "m", "prompt": "x"}),
                ("/api/pg/modules/media", {"action": "video", "model": "m", "prompt": "x"}),
                ("/api/pg/modules/speak", {"text": "x"}),
                ("/api/pg/modules/transcribe", {"audio": "x"}),
                ("/api/pg/modules/extract", {"name": "a.txt", "data": sample}),
                ("/api/pg/modules/notify", {"title": "t", "text": "b"}),
                ("/api/pg/modules/download", {"name": "x.txt", "text": "x"}),
                ("/api/pg/modules/handoff", {"code": "<h1>x</h1>"}),
            )
            for path, extra in probes:
                response = client.post(path, json={"module_id": "gated", **extra})
                self.assertEqual(response.status_code, 403, (path, response.text))
            loaded = client.get("/api/pg/modules/media-file",
                                params={"module_id": "gated", "url": "https://x/y.png"})
            self.assertEqual(loaded.status_code, 403, loaded.text)

    def test_persist_write_needs_storage(self) -> None:
        with self.client() as client:
            client.post("/api/pg/modules", json={
                "id": "chat-only", "title": "C", "js": "void 0",
                "persist": ["secret"], "permissions": ["chat"]})
            refused = client.post("/api/pg/modules/persist",
                                  json={"id": "chat-only", "fields": {"secret": "WRITTEN"}})
            self.assertEqual(refused.status_code, 403, refused.text)
            # Чтение свободно: оно ничего не меняет.
            self.assertEqual(client.post("/api/pg/modules/persist",
                                         json={"id": "chat-only"}).status_code, 200)

    def test_unticking_every_permission_denies_everything(self) -> None:
        """The workbench sends whatever is ticked; an empty list means nothing."""
        with self.client() as client:
            created = client.post("/api/pg/modules", json={
                "id": "denied", "title": "D", "js": "void 0", "persist": [],
                "permissions": []}).json()["module"]
            self.assertEqual(created["permissions"], [])
            for path, extra in (("/api/pg/modules/download", {"name": "a.txt", "text": "x"}),
                                ("/api/pg/modules/handoff", {"code": "<h1>x</h1>"})):
                response = client.post(path, json={"module_id": "denied", **extra})
                self.assertEqual(response.status_code, 403, (path, response.text))

    def test_env_cannot_inject_markup_into_the_preview(self) -> None:
        with self.client() as client:
            response = client.post("/api/pg/modules/preview", json={
                "id": "inj", "title": "I", "html": "<p>x</p>", "css": "", "js": "void 0",
                "env": {"lang": 'ru"><script>alert(2)</script><x y="',
                        "theme": 'dark" onload="alert(1)',
                        "surface": '"><img src=x onerror=alert(1)>',
                        "accent_name": "terracotta; } body { display:none }"},
            })
            self.assertEqual(response.status_code, 200)
            # Значения попали в атрибуты приведёнными, а не как есть.
            head = response.text.split("</head>", 1)[0]
            for danger in ("alert(2)", "onload=", "onerror=", "<script>alert", "img src=x"):
                self.assertNotIn(danger, head, danger)
            self.assertIn('data-theme="dark"', head)
            self.assertIn('data-accent="terracotta"', head)
            self.assertIn('data-surface=""', head)
            # Враждебный акцент не превратился в собственное правило CSS.
            self.assertNotIn("body { display:none }", head)

    def test_a_permission_name_is_not_accepted_where_an_action_belongs(self) -> None:
        """Passing `files` instead of `extract` used to allow everything silently."""
        from app.modules_host import ACTION_PERMISSION, PERMISSIONS
        both = {name for name in PERMISSIONS if name in ACTION_PERMISSION}
        # Права, которых нет среди действий: именно их передача была ошибкой.
        only_permissions = set(PERMISSIONS) - both
        self.assertTrue(only_permissions, "ожидались права без одноимённых действий")
        source = (Path(__file__).resolve().parents[1] / "app" / "playground_api.py")
        text = source.read_text(encoding="utf-8")
        # Каждый вызов _allowed должен передавать действие, а не право.
        for name in sorted(only_permissions):
            self.assertNotIn(f'_allowed(module_id, "{name}")', text,
                             f"_allowed вызван с правом {name!r} вместо действия")

    def test_the_host_bridge_enforces_the_same_permission_table(self) -> None:
        """Clipboard, models and chat history never reach a checked route.

        The window host therefore checks the table itself; it must be the
        exact table of modules_host, or a module could copy to the clipboard
        or read the model list without the matching permission.
        """
        import re
        from app.modules_host import ACTION_PERMISSION, BUILTIN_ORDER, load_package
        source = (Path(__file__).resolve().parents[1] / "app" / "pgstatic" / "modules.js").read_text(encoding="utf-8")
        block = re.search(r"const ACTION_PERMISSION = \{(.*?)\};", source, re.S)
        self.assertIsNotNone(block, "modules.js lost its permission table")
        table = dict(re.findall(r"(\w+):\s*\"([\w-]+)\"", block.group(1)))  # «net-import» has a hyphen
        self.assertEqual(table, ACTION_PERMISSION)
        self.assertIn("ACTION_PERMISSION[data.action]", source)
        # Every shipped module holds the permissions its own code relies on.
        needs = {"Memu.save": "storage", "Memu.copy": "clipboard", "Memu.models": "models",
                 "Memu.stream": "chat", "Memu.request": "chat", "Memu.image": "media",
                 "Memu.video": "media", "Memu.speak": "media", "Memu.transcribe": "media",
                 "Memu.extract": "files", "Memu.download": "download", "Memu.handoff": "download",
                 "Memu.fetchImport": "net-import"}
        with tempfile.TemporaryDirectory() as temp:
            for module_id in BUILTIN_ORDER:
                package = load_package(Path(temp), module_id)
                for call, permission in needs.items():
                    if call + "(" in package["js"]:
                        self.assertIn(permission, package["permissions"],
                                      f"{module_id} calls {call} without {permission!r}")


if __name__ == "__main__":
    unittest.main()