"""The module host is the real surface every module — built-in or custom — sits on."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app import modules_host as host
from app.modules_host import (ACTION_PERMISSION, BUILTIN_ORDER, PERMISSIONS, TEMPLATES,
                              build_request, clear_persistent, configure_module,
                              create_module, delete_module, design_handoff, duplicate_module,
                              export_package, import_package, list_modules, load_package,
                              load_persistent, media_routes, module_document, normalize_manifest,
                              normalize_window, remember_window, reorder_modules, resolve_window,
                              reset_override, save_persistent, template_manifest, update_code,
                              validate_module_source, window_policy)


ROOT = Path(__file__).resolve().parents[1]


class ModuleHostContracts(unittest.TestCase):
    def test_builtins_start_disabled_and_round_trip_launcher_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rows = list_modules(root)
            self.assertEqual([row["id"] for row in rows], list(BUILTIN_ORDER))
            self.assertTrue(all(row["builtin"] for row in rows))
            self.assertTrue(all(row["enabled"] is False for row in rows))
            changed = configure_module(root, "studio", enabled=True, hidden=True, slot="topbar")
            self.assertTrue(changed["enabled"])
            self.assertTrue(changed["launcher"]["hidden"])
            self.assertEqual(changed["launcher"]["slot"], "topbar")
            again = next(row for row in list_modules(root) if row["id"] == "studio")
            self.assertEqual(again["launcher"]["slot"], "topbar")
            self.assertFalse(next(row for row in list_modules(root)
                                  if row["id"] == "translator")["enabled"])

    def test_every_builtin_ships_a_complete_package(self) -> None:
        """A built-in without its files opens an empty window in the packaged app."""
        for module_id in BUILTIN_ORDER:
            folder = host.builtin_dir() / module_id
            self.assertTrue(folder.is_dir(), f"{module_id} folder missing")
            for name in ("manifest.json", "index.html", "style.css", "main.js"):
                self.assertTrue((folder / name).is_file(), f"{module_id}/{name} missing")
            package = host.load_package(Path(tempfile.mkdtemp()), module_id)
            self.assertFalse(package["missing"], f"{module_id} package is incomplete")
            self.assertGreater(len(package["html"]), 400)
            self.assertGreater(len(package["js"]), 1200)
            self.assertTrue(package["permissions"])
            self.assertIn("Memu.", package["js"])

    def test_builtin_dir_is_frozen_aware(self) -> None:
        """PyInstaller puts the packages next to the bundled app: read them there."""
        source = (ROOT / "app" / "modules_host.py").read_text(encoding="utf-8")
        self.assertIn("sys._MEIPASS", source)
        spec = (ROOT / "MemuAI.spec").read_text(encoding="utf-8")
        self.assertIn("app/module_builtins", spec)

    def test_window_policy_respects_fixed_authored_and_user_sizes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            rows = list_modules(Path(temp))
        studio = next(row for row in rows if row["id"] == "studio")
        translator = next(row for row in rows if row["id"] == "translator")

        # Every window drags; resizing is on by default and the person's choice.
        chosen = window_policy(translator, {"width": 500, "height": 360})
        self.assertTrue(chosen["resizable"])
        self.assertTrue(chosen["draggable"])
        self.assertEqual(chosen["size_source"], "user")
        self.assertEqual((chosen["width"], chosen["height"]), (500, 360))
        locked = window_policy(translator, {"width": 500, "height": 360}, resizable=False)
        self.assertFalse(locked["resizable"])
        self.assertTrue(locked["draggable"])
        self.assertEqual(locked["size_source"], "authored")

        # size_mode authored opens at the authored size unless the person resized it.
        authored = window_policy({**translator, "window": {**translator["window"],
                                                           "size_mode": "authored"}},
                                 {"width": 500, "height": 360})
        self.assertEqual(authored["size_source"], "authored")
        self.assertEqual(authored["width"], translator["window"]["default_width"])
        resized = window_policy({**translator, "window": {**translator["window"],
                                                          "size_mode": "authored"}},
                                {"width": 500, "height": 360, "resized": True})
        self.assertEqual(resized["width"], 500)

        # An unmoved window is centred: a stale x/y never pins it to a corner.
        self.assertTrue(window_policy(studio, {"x": 0, "y": 0})["center"])
        moved = window_policy(studio, {"x": 40, "y": 30, "moved": True})
        self.assertFalse(moved["center"])
        self.assertEqual((moved["x"], moved["y"]), (40, 30))

    def test_remember_window_keeps_centre_and_resizable_choice(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            configure_module(root, "design", enabled=True)
            policy = remember_window(root, "design", {"width": 900, "height": 600, "x": -1, "y": -1})
            self.assertTrue(policy["center"])
            self.assertEqual(policy["x"], -1)
            configure_module(root, "design", resizable=False)
            self.assertFalse(resolve_window(root, "design")["resizable"])
            row = next(item for item in list_modules(root) if item["id"] == "design")
            self.assertFalse(row["resizable"])

    def test_packaging_includes_the_builtin_packages(self) -> None:
        """Both build entry points must ship app/module_builtins.

        Round 7's report was exactly this: the installed .exe had no
        ``_internal/app/module_builtins``, so every built-in module opened an
        empty window. ``MemuAI.spec`` and ``build_exe.bat`` are separate build
        paths, so both are checked — fixing only one leaves the other broken.
        """
        spec = (ROOT / "MemuAI.spec").read_text(encoding="utf-8")
        self.assertIn("app/module_builtins", spec)
        build = (ROOT / "build.py").read_text(encoding="utf-8")
        self.assertIn('"app/module_builtins;app/module_builtins"', build)
        self.assertIn("build.py", (ROOT / "build_exe.bat").read_text(encoding="utf-8"))
        self.assertTrue((ROOT / "tests" / "frozen_modules_check.py").is_file())
        source = (ROOT / "app" / "modules_host.py").read_text(encoding="utf-8")
        self.assertIn("sys._MEIPASS", source)

    def test_an_id_cannot_escape_the_module_root(self) -> None:
        """A single request used to blank any reachable .json through `..`."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "modules"
            root.mkdir()
            victim = Path(temp) / "config.json"
            original = '{"providers":{"genspark":"token-abc"},"port":8317}'
            victim.write_text(original, encoding="utf-8")
            list_modules(root)
            for bad in ("../../config", "..", "../config", "a/../../b", "CONFIG",
                        "with space", "with_underscore"):
                with self.assertRaises(ValueError, msg=f"{bad!r} was accepted"):
                    save_persistent(root, bad, {"x": 1})
                with self.assertRaises(ValueError, msg=f"{bad!r} was accepted"):
                    load_persistent(root, bad)
                with self.assertRaises(ValueError, msg=f"{bad!r} was accepted"):
                    remember_window(root, bad, {"width": 10, "height": 10})
            self.assertEqual(victim.read_text(encoding="utf-8"), original)

    def test_persist_needs_the_storage_permission(self) -> None:
        """`save` maps to `storage`; without it nothing may be written."""
        self.assertEqual(ACTION_PERMISSION["save"], "storage")
        source = (ROOT / "app" / "playground_api.py").read_text(encoding="utf-8")
        start = source.index('"/api/pg/modules/persist"')
        handler = source[start:start + 1600]
        self.assertIn('_allowed(module_id, "save")', handler)

    def test_an_empty_permission_list_denies_everything(self) -> None:
        """Unticking every box is the strongest "no", not "yes to all"."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create_module(root, {"id": "deny-all", "title": "N", "js": "void 0",
                                 "persist": [], "permissions": []})
            self.assertEqual(load_package(root, "deny-all")["permissions"], [])
            absent = normalize_manifest({"id": "old-mod", "title": "O"}, module_id="old-mod")
            # «Все» права, кроме сетевого импорта (Round 29) и музыкальной библиотеки (Round 37): их модуль объявляет явно.
            self.assertEqual(sorted(absent["permissions"]), sorted(p for p in PERMISSIONS if p not in ("net-import", "music", "learn")))

    def test_env_cannot_inject_markup_into_the_document(self) -> None:
        """The preview route accepts env from a request: it must not reach HTML."""
        with tempfile.TemporaryDirectory() as temp:
            package = load_package(Path(temp), "translator")
            page = module_document(package, {}, env={
                "lang": 'ru"><script>alert(2)</script><x y="',
                "theme": 'dark" onload="alert(1)',
                "accent_name": "terracotta; } body { display:none }",
                "surface": '"><img src=x onerror=alert(1)>',
                "density": "normal",
                "font_scale": "999999",
                "accent": "#fff}body{display:none}",
            })
            self.assertNotIn("alert(", page)
            self.assertNotIn("onerror=", page)
            self.assertNotIn("onload=", page)
            self.assertIn('data-theme="dark"', page)
            self.assertIn('data-accent="terracotta"', page)
            self.assertIn('data-surface=""', page)
            self.assertIn('data-density="normal"', page)
            self.assertIn('lang="ru"', page)
            self.assertIn("--font-scale:125", page)

    def test_a_document_name_cannot_forge_the_prompt_delimiter(self) -> None:
        body = build_request(model="m", text="hi", documents=[
            {"name": '"><script>alert(1)</script>', "text": "payload"}])
        content = body["messages"][-1]["content"]
        blob = content[0]["text"] if isinstance(content, list) else content
        self.assertNotIn("<script>", blob)
        self.assertNotIn('"><', blob)
        self.assertEqual(blob.count("</file>"), 1)
        blank = build_request(model="m", text="t", documents=[{"name": "  ", "text": "x"}])
        inner = blank["messages"][-1]["content"]
        inner = inner[0]["text"] if isinstance(inner, list) else inner
        self.assertIn('<file name="file">', inner)

    def test_the_bridge_stamps_every_message_with_its_module(self) -> None:
        """Cross-module data leaks came from trusting `openId` alone."""
        source = (ROOT / "app" / "modules_host.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("from: MODULE_ID"), 2)
        host_script = (ROOT / "app" / "pgstatic" / "modules.js").read_text(encoding="utf-8")
        self.assertIn("data.from", host_script)
        self.assertIn("claimed !== owner.id", host_script)
        for module_id in BUILTIN_ORDER:
            module = (ROOT / "app" / "module_builtins" / module_id / "main.js")
            self.assertIn("pagehide", module.read_text(encoding="utf-8"),
                          f"{module_id}: отложенная запись не сбрасывается при уходе страницы")

    def test_frozen_check_reads_the_shipped_packages(self) -> None:
        """The check itself must be runnable and refuse to run unfrozen."""
        completed = subprocess.run(
            [sys.executable, str(ROOT / "tests" / "frozen_modules_check.py")],
            cwd=str(ROOT), check=False, capture_output=True, text=True, timeout=120)
        self.assertEqual(completed.returncode, 2, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertFalse(payload["frozen"])
        self.assertIn("frozen", payload["error"])

    # ------------------------------------------------------------ helpers

    def _root(self) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return Path(temp.name)

    @staticmethod
    def _custom(root: Path, module_id: str = "notes", **extra) -> dict:
        spec = {"id": module_id, "title": "Заметки", "html": "<p id='x'>x</p>",
                "css": "p{color:red}", "js": "Memu.ready();",
                "persist": ["draft", "model"], "permissions": ["storage", "chat"]}
        spec.update(extra)
        return create_module(root, spec)

    # --------------------------------------------------------- id validation

    def test_bad_ids_are_refused_everywhere(self) -> None:
        root = self._root()
        for bad in ("", "  ", "1abc", "ABC", "Abc", "a_b", "a b", "-a", "a/b", "a\\b",
                    "../x", "мод", "a" * 42, "a.b"):
            with self.assertRaises(ValueError, msg=f"{bad!r} was accepted"):
                self._custom(root, bad)
            with self.assertRaises(ValueError, msg=f"{bad!r} was accepted by validate_id"):
                host.validate_id(bad)
        self.assertEqual(host.validate_id("a" * 41), "a" * 41)
        self.assertEqual(host.validate_id(" my-app2 "), "my-app2")
        self.assertFalse((root / "custom").exists() and any((root / "custom").iterdir()))

    def test_builtin_ids_are_taken(self) -> None:
        root = self._root()
        for module_id in BUILTIN_ORDER:
            with self.assertRaises(ValueError, msg=module_id):
                self._custom(root, module_id)
            with self.assertRaises(ValueError, msg=module_id):
                import_package(root, {"manifest": {"id": module_id, "title": "x"},
                                      "files": {"main.js": "Memu.ready()"}})
            with self.assertRaises(ValueError, msg=module_id):
                duplicate_module(root, "translator", module_id)

    def test_duplicate_ids_are_refused(self) -> None:
        root = self._root()
        self._custom(root, "notes")
        with self.assertRaises(ValueError):
            self._custom(root, "notes", title="Другие")
        self.assertEqual([row["id"] for row in list_modules(root) if not row["builtin"]], ["notes"])

    def test_created_module_is_complete_and_starts_disabled(self) -> None:
        root = self._root()
        row = self._custom(root, "notes", launcher={"slot": "topbar", "hidden": True})
        self.assertFalse(row["enabled"])
        self.assertFalse(row["builtin"])
        self.assertEqual(row["launcher"]["slot"], "topbar")
        self.assertTrue(row["launcher"]["hidden"])
        folder = root / "custom" / "notes"
        for name in ("manifest.json", "index.html", "style.css", "main.js"):
            self.assertTrue((folder / name).is_file(), name)
        package = load_package(root, "notes")
        self.assertEqual(package["js"], "Memu.ready();")
        self.assertEqual(package["permissions"], ["storage", "chat"])
        self.assertFalse(package["missing"])

    def test_template_manifest_builds_working_modules(self) -> None:
        root = self._root()
        for index, template in enumerate(TEMPLATES):
            spec = template_manifest(template["id"], f"tpl-{index}", "")
            row = create_module(root, spec)
            self.assertEqual(row["title"], template["title_ru"])
            checked = validate_module_source(spec)
            self.assertTrue(checked["ok"], (template["id"], checked["problems"]))
        with self.assertRaises(KeyError):
            template_manifest("no-such-template", "x", "")

    # ------------------------------------------------- built-in code overrides

    def test_update_code_of_builtin_writes_an_override(self) -> None:
        root = self._root()
        shipped = (host.builtin_dir() / "translator" / "main.js").read_text(encoding="utf-8")
        row = update_code(root, "translator", {"js": "Memu.ready(); // mine"})
        self.assertTrue(row["edited"])
        self.assertTrue(row["builtin"])
        override = root / "overrides" / "translator"
        self.assertEqual((override / "main.js").read_text(encoding="utf-8"), "Memu.ready(); // mine")
        # The installed package is never touched.
        self.assertEqual((host.builtin_dir() / "translator" / "main.js").read_text(encoding="utf-8"),
                         shipped)
        package = load_package(root, "translator")
        self.assertEqual(package["js"], "Memu.ready(); // mine")
        # Untouched files come from the shipped package, not empty.
        self.assertGreater(len(package["html"]), 400)
        self.assertTrue(package["edited"])

    def test_update_code_keeps_earlier_edits_of_a_builtin(self) -> None:
        """Saving only the code must not bring back the shipped title."""
        root = self._root()
        update_code(root, "translator", {"title": "Мой переводчик",
                                         "window": {"default_width": 700, "default_height": 500}})
        update_code(root, "translator", {"js": "Memu.ready();"})
        package = load_package(root, "translator")
        self.assertEqual(package["title"], "Мой переводчик")
        self.assertEqual(package["window"]["default_width"], 700)
        self.assertEqual(package["js"], "Memu.ready();")

    def test_reset_override_returns_the_shipped_package(self) -> None:
        root = self._root()
        shipped = load_package(root, "studio")["js"]
        update_code(root, "studio", {"js": "Memu.ready();", "title": "Изменённая"})
        row = reset_override(root, "studio")
        self.assertFalse(row["edited"])
        self.assertFalse((root / "overrides" / "studio").exists())
        package = load_package(root, "studio")
        self.assertEqual(package["js"], shipped)
        self.assertEqual(package["title"], "MemuAI Studio")
        # Resetting twice is harmless.
        self.assertFalse(reset_override(root, "studio")["edited"])

    def test_reset_override_is_only_for_builtins(self) -> None:
        root = self._root()
        self._custom(root, "notes")
        with self.assertRaises(ValueError):
            reset_override(root, "notes")
        with self.assertRaises(ValueError):
            reset_override(root, "../studio")

    def test_update_code_of_a_custom_module(self) -> None:
        root = self._root()
        self._custom(root, "notes")
        row = update_code(root, "notes", {"html": "<b>new</b>", "title": "Новые заметки"})
        self.assertEqual(row["title"], "Новые заметки")
        self.assertFalse(row["edited"])
        package = load_package(root, "notes")
        self.assertEqual(package["html"], "<b>new</b>")
        self.assertEqual(package["js"], "Memu.ready();")
        with self.assertRaises(KeyError):
            update_code(root, "missing-module", {"js": "x"})
        with self.assertRaises(ValueError):
            update_code(root, "notes", {"js": "x" * (host._CODE_LIMIT + 1)})

    # ------------------------------------------------------------- deletion

    def test_delete_module_removes_package_state_and_saved_fields(self) -> None:
        root = self._root()
        self._custom(root, "notes")
        configure_module(root, "notes", enabled=True, slot="composer")
        save_persistent(root, "notes", {"draft": "текст"})
        delete_module(root, "notes")
        self.assertFalse((root / "custom" / "notes").exists())
        self.assertFalse((root / "persist" / "notes.json").exists())
        self.assertNotIn("notes", [row["id"] for row in list_modules(root)])
        state = json.loads((root / "state.json").read_text(encoding="utf-8"))
        self.assertNotIn("notes", state["modules"])
        # The same id can be used again afterwards and starts clean.
        self._custom(root, "notes")
        self.assertEqual(load_persistent(root, "notes"), {})
        self.assertFalse(next(row for row in list_modules(root) if row["id"] == "notes")["enabled"])

    def test_builtins_can_be_removed_and_restored(self) -> None:
        root = self._root()
        configure_module(root, "studio", enabled=True, slot="topbar")
        update_code(root, "studio", {"js": "Memu.ready();"})
        save_persistent(root, "studio", {"prompt": "кот"})
        delete_module(root, "studio")
        self.assertNotIn("studio", [row["id"] for row in list_modules(root)])
        self.assertEqual([row["id"] for row in host.removed_builtins(root)], ["studio"])
        self.assertFalse((root / "overrides" / "studio").exists())
        with self.assertRaises(KeyError):
            load_package(root, "studio")
        with self.assertRaises(KeyError):
            delete_module(root, "studio")
        with self.assertRaises(KeyError):
            configure_module(root, "studio", enabled=True)
        # The id stays reserved even while the built-in is removed.
        with self.assertRaises(ValueError):
            self._custom(root, "studio")
        row = host.restore_builtin(root, "studio")
        self.assertFalse(row["enabled"])
        self.assertFalse(row["edited"])
        self.assertEqual(row["launcher"]["slot"], "sidebar")
        self.assertEqual(load_persistent(root, "studio"), {})
        self.assertEqual(host.removed_builtins(root), [])
        with self.assertRaises(ValueError):
            host.restore_builtin(root, "notes")

    def test_builtins_ship_as_version_one(self) -> None:
        root = self._root()
        for row in list_modules(root):
            self.assertEqual(row["version"], "1.0", row["id"])

    def test_unknown_modules_cannot_be_deleted(self) -> None:
        root = self._root()
        with self.assertRaises(KeyError):
            delete_module(root, "never-existed")
        with self.assertRaises(ValueError):
            delete_module(root, "../custom")

    # ------------------------------------------------------ persistent fields

    def test_only_declared_keys_are_saved(self) -> None:
        root = self._root()
        self._custom(root, "notes")
        saved = save_persistent(root, "notes", {"draft": "a", "model": "m", "secret": "x",
                                                "__proto__": 1})
        self.assertEqual(saved, {"draft": "a", "model": "m"})
        self.assertEqual(load_persistent(root, "notes"), {"draft": "a", "model": "m"})
        # A second save merges instead of replacing.
        save_persistent(root, "notes", {"draft": "b"})
        self.assertEqual(load_persistent(root, "notes"), {"draft": "b", "model": "m"})

    def test_undeclared_keys_on_disk_are_never_loaded(self) -> None:
        root = self._root()
        self._custom(root, "notes")
        (root / "persist").mkdir(parents=True, exist_ok=True)
        (root / "persist" / "notes.json").write_text(
            json.dumps({"draft": "ok", "token": "leak"}), encoding="utf-8")
        self.assertEqual(load_persistent(root, "notes"), {"draft": "ok"})
        # Dropping a key from the manifest hides its old value too.
        update_code(root, "notes", {"persist": ["model"]})
        self.assertEqual(load_persistent(root, "notes"), {})

    def test_save_persistent_validates_input_and_size(self) -> None:
        root = self._root()
        self._custom(root, "notes")
        with self.assertRaises(ValueError):
            save_persistent(root, "notes", ["draft"])
        original = host._TEXT_LIMIT
        host._TEXT_LIMIT = 20
        try:
            # An oversized field is reported; the fields that fit are still saved.
            with self.assertRaises(ValueError):
                save_persistent(root, "notes", {"draft": "x" * 100, "model": "short"})
        finally:
            host._TEXT_LIMIT = original
        self.assertEqual(host.load_persistent(root, "notes"), {"model": "short"})

    def test_clear_persistent_removes_everything_and_is_idempotent(self) -> None:
        root = self._root()
        self._custom(root, "notes")
        save_persistent(root, "notes", {"draft": "a"})
        clear_persistent(root, "notes")
        self.assertFalse((root / "persist" / "notes.json").exists())
        self.assertEqual(load_persistent(root, "notes"), {})
        clear_persistent(root, "notes")
        with self.assertRaises(ValueError):
            clear_persistent(root, "../notes")

    def test_builtin_saved_fields_follow_its_manifest(self) -> None:
        root = self._root()
        saved = save_persistent(root, "translator", {"source": "hi", "nope": 1})
        self.assertEqual(saved, {"source": "hi"})
        self.assertEqual(load_package(root, "translator")["saved"], {"source": "hi"})

    # ------------------------------------------------------------ validation

    def test_validation_warns_about_markup_that_breaks_the_document(self) -> None:
        checked = validate_module_source({
            "html": "<div></div></body></html><script src='x.js'></script>",
            "css": "a{}</style>",
            "js": "const s = '</script>'; Memu.request({})",
        })
        fields = sorted(item["field"] for item in checked["problems"] if item["level"] == "warn")
        self.assertEqual(fields, ["css", "html", "html", "js"])
        self.assertFalse(checked["ok"])
        for item in checked["problems"]:
            self.assertTrue(item["ru"] and item["en"])

    def test_validation_notes_urls_empty_code_and_no_model(self) -> None:
        urls = validate_module_source({"js": "fetch('https://example.com'); Memu.request({})"})
        self.assertTrue(urls["ok"])
        self.assertEqual([item["level"] for item in urls["problems"]], ["info"])
        self.assertIn("https://", urls["problems"][0]["ru"])
        empty = validate_module_source({"html": "<p>x</p>", "js": "  "})
        self.assertTrue(any(item["field"] == "js" and item["level"] == "info"
                            for item in empty["problems"]))
        quiet = validate_module_source({"js": "Memu.copy('x')"})
        self.assertTrue(any("не обращается к моделям" in item["ru"] for item in quiet["problems"]))
        self.assertTrue(quiet["ok"])

    def test_validation_finds_every_saved_key_that_will_not_persist(self) -> None:
        checked = validate_module_source({
            "js": "Memu.request({}); Memu.save({ draft, model: m, \"tone\": t, list: f(a, b), ...rest });",
            "persist": ["draft"],
        })
        warn = [item for item in checked["problems"] if item["field"] == "persist"]
        self.assertEqual(len(warn), 1)
        self.assertIn("list, model, tone", warn[0]["ru"])
        self.assertNotIn("draft", warn[0]["ru"])
        self.assertNotIn("rest", warn[0]["ru"])
        ok = validate_module_source({"js": "Memu.request({}); Memu.save({ a, b })",
                                     "persist": ["a", "b"]})
        self.assertEqual(ok["problems"], [])
        self.assertTrue(ok["ok"])

    # ---------------------------------------------------- export and import

    def test_export_then_import_round_trips_a_module(self) -> None:
        root = self._root()
        self._custom(root, "notes", bridge={"instructions": "Будь краток"},
                     window={"default_width": 640, "default_height": 480})
        package = export_package(root, "notes")
        self.assertEqual(package["format"], host.PACKAGE_FORMAT)
        self.assertEqual(package["version"], host.PACKAGE_VERSION)
        self.assertEqual(set(package["files"]), {"index.html", "style.css", "main.js", "i18n.json"})
        encoded = json.loads(json.dumps(package, ensure_ascii=False))
        row = import_package(root, encoded, new_id="notes-copy", title="Копия")
        self.assertEqual(row["id"], "notes-copy")
        self.assertEqual(row["title"], "Копия")
        self.assertFalse(row["enabled"])
        copy = load_package(root, "notes-copy")
        original = load_package(root, "notes")
        for key in ("html", "css", "js", "persist", "permissions", "i18n", "default_locale"):
            self.assertEqual(copy[key], original[key], key)
        self.assertEqual(copy["bridge"]["instructions"], "Будь краток")
        self.assertEqual(copy["window"]["default_width"], 640)

    def test_builtins_export_too(self) -> None:
        root = self._root()
        package = export_package(root, "design")
        self.assertEqual(package["manifest"]["id"], "design")
        row = import_package(root, package, new_id="my-design")
        self.assertFalse(row["builtin"])
        self.assertEqual(load_package(root, "my-design")["js"], load_package(root, "design")["js"])

    def test_import_refuses_broken_packages(self) -> None:
        root = self._root()
        self._custom(root, "notes")
        broken = [
            ["not", "an", "object"],
            {},
            {"manifest": {"id": "no-code", "title": "x"}, "files": {}},
            {"manifest": {"id": "BAD ID"}, "files": {"main.js": "x"}},
            {"manifest": {"id": "notes"}, "files": {"main.js": "x"}},
        ]
        for payload in broken:
            with self.assertRaises(ValueError, msg=repr(payload)[:60]):
                import_package(root, payload)
        # A flat package (no "manifest" key) still imports.
        row = import_package(root, {"id": "flat", "title": "Плоский", "js": "Memu.ready()",
                                    "enabled": True})
        self.assertEqual(row["id"], "flat")
        self.assertFalse(row["enabled"], "an imported module must start disabled")

    # ------------------------------------------------------------- duplicate

    def test_duplicate_copies_code_and_settings(self) -> None:
        root = self._root()
        row = duplicate_module(root, "translator", "translator-2")
        self.assertFalse(row["builtin"])
        self.assertEqual(row["title"], "Переводчик 2")
        self.assertEqual(row["title_en"], "Translator 2")
        copy, source = load_package(root, "translator-2"), load_package(root, "translator")
        for key in ("html", "css", "js", "persist", "permissions", "window"):
            self.assertEqual(copy[key], source[key], key)
        named = duplicate_module(root, "translator", "translator-3", "Мой")
        self.assertEqual((named["title"], named["title_en"]), ("Мой", "Мой"))
        with self.assertRaises(ValueError):
            duplicate_module(root, "translator", "translator-2")
        with self.assertRaises(KeyError):
            duplicate_module(root, "ghost", "ghost-2")

    def test_duplicate_of_an_edited_builtin_copies_the_edit(self) -> None:
        root = self._root()
        update_code(root, "studio", {"js": "Memu.ready(); // edited"})
        duplicate_module(root, "studio", "studio-copy")
        self.assertEqual(load_package(root, "studio-copy")["js"], "Memu.ready(); // edited")

    # --------------------------------------------------------------- order

    def test_reorder_applies_the_listed_order_and_keeps_the_rest(self) -> None:
        root = self._root()
        self._custom(root, "notes")
        rows = reorder_modules(root, ["notes", "design", "ghost", "notes"])
        self.assertEqual([row["id"] for row in rows], ["notes", "design", "translator", "studio", "benchmarks", "catalog", "api-models", "characters", "music", "learn"])
        self.assertEqual([row["launcher"]["order"] for row in rows], list(range(len(rows))))
        again = reorder_modules(root, ["studio"])
        self.assertEqual([row["id"] for row in again][0], "studio")
        self.assertEqual(sorted(row["id"] for row in again),
                         sorted(["notes", "design", "translator", "studio", "benchmarks", "catalog", "api-models", "characters", "music", "learn"]))
        self.assertEqual([row["id"] for row in reorder_modules(root, [])],
                         [row["id"] for row in again])

    # ------------------------------------------------------------ the bridge

    def test_build_request_shapes_the_conversation(self) -> None:
        body = build_request(
            model=" m " + "x" * 400, instructions="Будь краток", text="Вопрос",
            history=[{"role": "user", "content": "первый"},
                     {"role": "assistant", "content": "ответ"},
                     {"role": "system", "content": "станет user"},
                     {"role": "assistant", "content": "   "},
                     "не объект",
                     {"role": "user", "content": "Вопрос"}],
            temperature=9, thinking=1, web_search="yes")
        self.assertEqual(len(body["model"]), 300)
        roles = [message["role"] for message in body["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "user", "user"])
        self.assertEqual(body["messages"][-1]["content"], "Вопрос")
        # The last user turn in history is the current text: it is not sent twice.
        self.assertEqual(sum(1 for m in body["messages"] if m["content"] == "Вопрос"), 1)
        self.assertEqual(body["temperature"], 2.0)
        self.assertTrue(body["thinking"] and body["web_search"])
        self.assertNotIn("temperature", build_request(text="x", temperature="warm"))
        self.assertNotIn("system", [m["role"] for m in build_request(text="x")["messages"]])

    def test_build_request_filters_images_and_documents(self) -> None:
        body = build_request(model="m", text="Посмотри",
                             images=["data:image/png;base64,AAA", "https://evil/x.png",
                                     "javascript:alert(1)", 42] + ["data:image/png;base64,B"] * 10,
                             documents=[{"name": "a.txt", "text": "A"}, "junk", {"text": "B"}])
        self.assertEqual(len(body["images"]), 8, "junk must not eat the image limit")
        self.assertTrue(all(url.startswith("data:image/") for url in body["images"]))
        content = body["messages"][-1]["content"]
        self.assertIsInstance(content, list)
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(sum(1 for part in content if part["type"] == "image_url"), 8)
        self.assertIn('<file name="a.txt">', content[0]["text"])
        self.assertIn('<file name="file">', content[0]["text"])
        self.assertEqual(len(body["documents"]), 2)
        plain = build_request(model="m", text="t")
        self.assertIsInstance(plain["messages"][-1]["content"], str)

    def test_design_handoff_bundles_safe_files(self) -> None:
        bundle = design_handoff(prompt="Сайт кофейни", code="<html></html>",
                                images=["data:image/png;base64,AAA", "http://x/y.png"],
                                files=[{"name": "../../evil.html", "content": "x"},
                                       {"name": 'a:b*c?.css', "content": "y"},
                                       {"name": "", "content": "z"}, "junk"])
        names = [item["name"] for item in bundle["files"]]
        self.assertEqual(len(names), 3)
        for name in names:
            self.assertNotRegex(name, r'[\\/:*?"<>|]')
            self.assertFalse(name.startswith("."))
        self.assertEqual(bundle["images"], ["data:image/png;base64,AAA"])
        self.assertIn("Изображений в пакете: 1", bundle["readme"])
        self.assertIn("Сайт кофейни", bundle["readme"])
        single = design_handoff(prompt="p", code="<p>code</p>", filename="page.html")
        self.assertEqual(single["files"], [{"name": "page.html", "content": "<p>code</p>"}])
        fallback = design_handoff(code="c", filename='///')
        self.assertEqual(fallback["files"][0]["name"], "index.html")

    # --------------------------------------------------------------- window

    def test_normalize_window_is_never_degenerate(self) -> None:
        for spec in (None, "x", {}, {"default_width": 10 ** 9, "default_height": -5},
                     {"min_width": 5000, "max_width": 100, "default_width": 800},
                     {"width": "abc", "height": None},
                     {"min_width": 2000, "max_width": 2000, "default_width": 2400,
                      "min_height": 1200, "max_height": 1200, "default_height": 1600}):
            window = normalize_window(spec)
            self.assertLessEqual(window["min_width"], window["default_width"], spec)
            self.assertLessEqual(window["default_width"], window["max_width"], spec)
            self.assertLessEqual(window["min_height"], window["default_height"], spec)
            self.assertLessEqual(window["default_height"], window["max_height"], spec)
            self.assertGreater(window["max_width"], window["min_width"], spec)
            self.assertGreater(window["max_height"], window["min_height"], spec)
            self.assertTrue(200 <= window["min_width"] <= 2000)
            self.assertTrue(window["max_width"] <= 4000)

    def test_normalize_window_defaults_and_flags(self) -> None:
        window = normalize_window({})
        self.assertEqual((window["default_width"], window["default_height"]), (760, 540))
        self.assertTrue(window["resizable"])
        self.assertFalse(window["fixed"])
        self.assertEqual(window["size_mode"], "user")
        self.assertTrue(window["center"])
        self.assertTrue(window["fullscreen_allowed"])
        fixed = normalize_window({"fixed": True, "resizable": True, "size_mode": "authored",
                                  "fullscreen_allowed": False})
        self.assertFalse(fixed["resizable"])
        self.assertEqual(fixed["size_mode"], "authored")
        self.assertFalse(fixed["fullscreen_allowed"])
        self.assertEqual(normalize_window({"size_mode": "weird"})["size_mode"], "user")
        small = normalize_window({"default_width": 300, "default_height": 220})
        self.assertLessEqual(small["min_width"], 300)
        self.assertLessEqual(small["min_height"], 220)
        legacy = normalize_window({"width": 900, "height": 700})
        self.assertEqual((legacy["default_width"], legacy["default_height"]), (900, 700))

    def test_code_ending_in_a_line_comment_still_runs(self) -> None:
        """`function(){` + js + `};` let a trailing `// ...` comment out the `};`."""
        page = module_document({"id": "x", "html": "<p></p>", "js": "Memu.ready(); // конец"}, {})
        script = page.split("window.__memuModuleMain=function(){", 1)[1].split("</script>", 1)[0]
        self.assertTrue(script.rstrip().endswith("};"))
        self.assertIn("// конец\n};", script)

    def test_templates_carry_their_permissions_to_the_wizard(self) -> None:
        """The wizard used to create a «Мини-чат» without `models`."""
        rows = {row["id"]: row for row in host.templates()}
        for template in TEMPLATES:
            self.assertEqual(rows[template["id"]]["permissions"], list(template["permissions"]))
            self.assertEqual(rows[template["id"]]["persist"], list(template["persist"]))
            for call, permission in (("Memu.models(", "models"), ("Memu.save(", "storage"),
                                     ("Memu.stream(", "chat"), ("Memu.image(", "media")):
                if call in template["js"]:
                    self.assertIn(permission, template["permissions"], (template["id"], call))


if __name__ == "__main__":
    unittest.main()
