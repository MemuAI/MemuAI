"""Round 29 (r29-quick-explorer): Quick Ask over File Explorer / other programs + the window extras.

Pure logic and the Linux-safe paths are tested directly; the Windows half (Shell COM, Office COM, Windows OCR,
pixels) is replaced with fakes. Run: ``python3 -m pytest -q tests/round29_quick_explorer_test.py``.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

if not os.environ.get("MEMUAI_DATA_DIR"):
    os.environ["MEMUAI_DATA_DIR"] = tempfile.mkdtemp(prefix="memuai-tests-")

os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp(prefix="memuai-tests-xdg-")

from app import quick_apps, quick_apps_win, quick_explorer as qe, quick_extras  # noqa: E402
from app import quick_ask_api  # noqa: E402
from app import quick_ask_core as core  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "app" / "pgstatic" / "quick" / "plugins"

def make_folder() -> Path:
    base = Path(tempfile.mkdtemp(prefix="qe-"))
    (base / "a.txt").write_text("hello\nworld\n", encoding="utf-8")
    (base / "b.txt").write_text("hello\nworld\n", encoding="utf-8")
    (base / "notes.md").write_text("# Title\nsome foo text\n", encoding="utf-8")
    (base / "sub").mkdir()
    (base / "sub" / "c.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    (base / "photo.jpg").write_bytes(b"\xff\xd8\xff\x00binary")
    return base

def ctx_for(folder: Path, **extra) -> dict:
    return quick_apps.register_context({"kind": "explorer", "app": "Explorer", "exe": "explorer.exe",
                                        "folder": str(folder), **extra})

class ClassifyTests(unittest.TestCase):
    def test_kinds(self) -> None:
        self.assertEqual(quick_apps.classify("CabinetWClass", r"C:\Windows\explorer.exe")["kind"], "explorer")
        self.assertEqual(quick_apps.classify("WorkerW", "explorer.exe")["kind"], "desktop")
        self.assertEqual(quick_apps.classify("Chrome_WidgetWin_1", r"C:\x\Code.exe")["kind"], "vscode")
        self.assertEqual(quick_apps.classify("Chrome_WidgetWin_1", "Cursor.exe")["app"], "Cursor")
        self.assertEqual(quick_apps.classify("Notepad++", "notepad++.exe")["kind"], "editor")
        self.assertEqual(quick_apps.classify("SunAwtFrame", "pycharm64.exe")["kind"], "jetbrains")
        self.assertEqual(quick_apps.classify("OpusApp", "WINWORD.EXE")["app"], "Word")
        self.assertEqual(quick_apps.classify("CASCADIA_HOSTING_WINDOW_CLASS", "WindowsTerminal.exe")["kind"], "terminal")
        self.assertEqual(quick_apps.classify("x", "MemuAI.exe")["kind"], "own")
        self.assertEqual(quick_apps.classify("x", "telegram.exe")["kind"], "app")

    def test_titles(self) -> None:
        p = quick_apps.parse_title("vscode", "code.exe", "● quick_ask.py - memu - Visual Studio Code")
        self.assertEqual((p["file"], p["folder"]), ("quick_ask.py", "memu"))
        p = quick_apps.parse_title("vscode", "code.exe", "proj (Workspace) - Visual Studio Code [Administrator]")
        self.assertEqual((p["file"], p["folder"]), ("", "proj"))
        p = quick_apps.parse_title("vscode", "code.exe", "Welcome - memu - Visual Studio Code")
        self.assertEqual(p["file"], "")
        p = quick_apps.parse_title("editor", "notepad++.exe", r"*C:\work\notes - draft.txt - Notepad++")
        self.assertEqual(p["path"], r"C:\work\notes - draft.txt")
        p = quick_apps.parse_title("editor", "sublime_text.exe", r"C:\a\b.py (a) - Sublime Text")
        self.assertEqual(p["file"], "b.py")
        p = quick_apps.parse_title("editor", "notepad.exe", "*todo.txt - Notepad")
        self.assertEqual((p["file"], p["path"]), ("todo.txt", ""))
        p = quick_apps.parse_title("editor", "notepad.exe", "Untitled - Notepad")
        self.assertEqual(p["file"], "")
        p = quick_apps.parse_title("jetbrains", "pycharm64.exe", "MemuAI – app/quick_ask.py")
        self.assertEqual((p["folder"], p["file"]), ("MemuAI", "quick_ask.py"))
        p = quick_apps.parse_title("office", "winword.exe", "Отчёт.docx [Режим совместимости] - Word")
        self.assertEqual(p["file"], "Отчёт.docx")

    def test_explorer_tab_by_title(self) -> None:
        rows = [{"path": r"C:\a", "name": "a"}, {"path": r"C:\Downloads", "name": "Downloads"}]
        self.assertEqual(quick_apps_win.pick_explorer_tab(rows, "Downloads - File Explorer")["path"], r"C:\Downloads")
        self.assertEqual(quick_apps_win.pick_explorer_tab(rows, "other")["path"], r"C:\a")
        self.assertIsNone(quick_apps_win.pick_explorer_tab([], "x"))

    def test_vscode_folder_and_file(self) -> None:
        appdata = Path(tempfile.mkdtemp())
        project = Path(tempfile.mkdtemp()) / "memu"
        (project / "app").mkdir(parents=True)
        (project / "app" / "quick_ask.py").write_text("x = 1\n", encoding="utf-8")
        (project / "node_modules").mkdir()
        (project / "node_modules" / "quick_ask.py").write_text("no\n", encoding="utf-8")
        storage = appdata / "Code" / "User" / "globalStorage"
        storage.mkdir(parents=True)
        uri = "file://" + ("/" if not str(project).startswith("/") else "") + str(project).replace(os.sep, "/")
        (storage / "storage.json").write_text(json.dumps({"windowsState": {"lastActiveWindow": {"folder": uri}}}), encoding="utf-8")
        self.assertIn(str(project), quick_apps.vscode_folders("Code", str(appdata)))
        found = quick_apps.resolve_editor_file("vscode", "Code", {"file": "quick_ask.py", "folder": "memu"}, [], str(appdata))
        self.assertEqual(found["folder"], str(project))
        self.assertEqual(found["path"], str(project / "app" / "quick_ask.py"))

    def test_editor_file_from_cmdline(self) -> None:
        folder = make_folder()
        found = quick_apps.resolve_editor_file("editor", "Notepad", {"file": "notes.md"}, ["notepad.exe", str(folder / "notes.md")])
        self.assertEqual(found["path"], str(folder / "notes.md"))

    def test_terminal_cwd_from_title(self) -> None:
        with mock.patch("psutil.Process", side_effect=RuntimeError), \
                mock.patch.object(quick_apps_win.os.path, "isdir", side_effect=lambda p: p == r"C:\work\proj"):
            self.assertEqual(quick_apps_win.terminal_cwd(1, "pwsh.exe", r"PS C:\work\proj>")["path"], r"C:\work\proj")
            self.assertEqual(quick_apps_win.terminal_cwd(1, "cmd.exe", "Command Prompt")["path"], "")

class SettingsTests(unittest.TestCase):
    def test_normalize(self) -> None:
        s = quick_apps.normalize_settings({"access": "root", "explorer": "false", "currency_to": "eur!", "notes_dir": "rel/x"})
        self.assertEqual((s["access"], s["explorer"], s["currency_to"], s["notes_dir"]), ("ask", False, "EUR", ""))
        rows = quick_apps.normalize_presets([{"name": "A", "prompt": "B", "apps": "Outlook, excel.exe"}, {"name": "", "prompt": "x"}, "junk"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["apps"], ["outlook.exe", "excel.exe"])
        self.assertTrue(rows[0]["id"].startswith("p_"))

    def test_save_and_presets_for(self) -> None:
        config = quick_apps.save_settings({"access": "full", "bogus": 1})
        self.assertEqual(config["settings"]["access"], "full")
        self.assertNotIn("bogus", config["settings"])
        quick_apps.save_settings({"access": "ask"})
        presets = quick_apps.default_presets("en")
        self.assertTrue(all(p["prompt"] for p in presets))
        self.assertEqual([p["name"] for p in quick_apps.presets_for({"exe": "excel.exe"}, presets)], ["Formula help"])

class PathSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = make_folder()
        self.ctx = ctx_for(self.folder)

    def test_escapes_are_refused(self) -> None:
        root = qe.folder_root(self.ctx)
        for bad in ("../x", "/etc/passwd", "C:/Windows", "a/../../x", "con.txt", "bad|name", "trailing."):
            with self.assertRaises(qe.FolderError, msg=bad):
                qe.inside(root, bad)
        self.assertEqual(qe.inside(root, "sub/c.csv"), root / "sub" / "c.csv")

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_symlink_escape(self) -> None:
        outside = Path(tempfile.mkdtemp())
        (outside / "secret.txt").write_text("s", encoding="utf-8")
        os.symlink(outside, self.folder / "link")
        root = qe.folder_root(self.ctx)
        with self.assertRaises(qe.FolderError):
            qe.inside(root, "link/secret.txt")
        listed = [row["path"] for row in qe.tool_list(self.ctx, {"depth": 3})["entries"]]
        self.assertFalse(any(p.startswith("link") for p in listed))
        with self.assertRaises(qe.FolderError):
            qe.tool_read(self.ctx, {"path": "link/secret.txt"})

    def test_protected_folders_are_read_only(self) -> None:
        self.assertTrue(quick_apps.is_protected(Path(os.path.abspath(os.sep))))
        if os.name != "nt":
            self.assertTrue(quick_apps.is_protected(Path("/usr/lib")))
        ctx = quick_apps.register_context({"kind": "explorer", "folder": str(self.folder)})
        quick_apps.update_context(ctx["id"], {"writable": False})
        with self.assertRaises(qe.FolderError) as cm:
            qe.plan_changes(ctx, [{"op": "mkdir", "path": "x"}])
        self.assertEqual(cm.exception.code, "read_only")

    def test_virtual_folder_is_not_a_folder(self) -> None:
        ctx = quick_apps.register_context({"kind": "explorer", "folder": "::{20D04FE0-3AEA-1069-A2D8-08002B30309D}"})
        self.assertEqual(ctx["folder"], "")
        with self.assertRaises(qe.FolderError):
            qe.folder_root(ctx)

class ToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = make_folder()
        self.ctx = ctx_for(self.folder, selected=[str(self.folder / "a.txt"), str(self.folder / "sub")])

    def test_list_read_search_info(self) -> None:
        entries = qe.tool_list(self.ctx, {"depth": 2})["entries"]
        self.assertIn("sub/c.csv", [e["path"] for e in entries])
        self.assertEqual(qe.tool_read(self.ctx, {"path": "notes.md"})["text"].splitlines()[0], "# Title")
        part = qe.tool_read(self.ctx, {"path": "notes.md", "start_line": 2, "end_line": 2})
        self.assertEqual(part["text"], "2: some foo text")
        with self.assertRaises(qe.FolderError):
            qe.tool_read(self.ctx, {"path": "photo.jpg"})
        found = qe.tool_search(self.ctx, {"query": "foo"})
        self.assertEqual(found["matches"][0]["path"], "notes.md")
        self.assertIn("photo.jpg", qe.tool_search(self.ctx, {"query": "photo", "names": True})["names"])
        info = qe.tool_info(self.ctx, {})
        self.assertEqual(info["files"], 5)
        self.assertEqual(info["selected"], ["a.txt", "sub/"])
        with self.assertRaises(qe.FolderError):
            qe.tool_search(self.ctx, {"query": "(a+)+", "regex": True})

    def test_duplicates(self) -> None:
        dups = qe.find_duplicates(self.ctx)
        expected_size = (self.folder / "a.txt").stat().st_size
        self.assertEqual(dups["groups"], [{"size": expected_size, "files": dups["groups"][0]["files"]}])
        self.assertEqual(sorted(dups["groups"][0]["files"]), ["a.txt", "b.txt"])

    def test_context_block_and_request_settings(self) -> None:
        block = qe.context_block(self.ctx, "en")
        self.assertIn("<folder_context>", block)
        self.assertIn("Selected in Explorer (2): a.txt, sub/", block)
        self.assertIn("notes.md (", block)
        out = qe.request_settings({"contexts": [{"type": "app", "id": self.ctx["id"]}]})
        self.assertEqual(out["_quick_app_ctx"], self.ctx["id"])
        self.assertEqual(out["_quick_tool_rounds"], qe.TOOL_ROUNDS)
        self.assertEqual(qe.request_settings({"contexts": [{"type": "app", "id": "nope"}]}), {})
        self.assertEqual(qe.request_settings({"contexts": "junk"}), {})

    def test_editor_context_includes_the_file(self) -> None:
        ctx = quick_apps.register_context({"kind": "vscode", "app": "Code", "folder": str(self.folder),
                                           "file": str(self.folder / "notes.md"), "file_name": "notes.md"})
        block = qe.context_block(ctx, "en")
        self.assertIn("<active_file>", block)
        self.assertIn("some foo text", block)

class ChangeSetTests(unittest.TestCase):
    def setUp(self) -> None:
        quick_apps.save_settings({"access": "ask"})
        self.folder = make_folder()
        self.ctx = ctx_for(self.folder)

    def test_plan_apply_undo(self) -> None:
        change = qe.plan_changes(self.ctx, [
            {"op": "edit", "path": "a.txt", "old_text": "world", "new_text": "there"},
            {"op": "mkdir", "path": "Texts"},
            {"op": "rename", "path": "b.txt", "to": "Texts/b.txt"},
            {"op": "write", "path": "new.md", "text": "# New\n"},
            {"op": "write", "path": "notes.md", "text": "replaced\n"},
            {"op": "delete", "path": "sub"},
            {"op": "write", "path": "../x", "text": "no"},
            {"op": "move", "path": "missing.txt", "to": "y.txt"},
        ], summary="test")
        public = qe.public_set(change)
        statuses = [op["status"] for op in public["ops"]]
        self.assertEqual(statuses, ["pending"] * 6 + ["invalid", "invalid"])
        self.assertEqual(public["ops"][4]["risky"], "overwrite")
        self.assertEqual(public["ops"][5]["risky"], "delete")
        self.assertEqual(public["ops"][6]["path"], "../x")
        self.assertIn("-world", public["ops"][0]["diff"])
        self.assertTrue(public["ops"][3]["preview"].startswith("# New"))

        self.assertEqual((self.folder / "a.txt").read_text(encoding="utf-8"), "hello\nworld\n")
        result = qe.apply_set(change["id"])
        self.assertEqual((result["applied"], result["failed"]), (6, 0))
        self.assertEqual((self.folder / "a.txt").read_text(encoding="utf-8"), "hello\nthere\n")
        self.assertTrue((self.folder / "Texts" / "b.txt").is_file())
        self.assertFalse((self.folder / "sub").exists())
        self.assertEqual((self.folder / "notes.md").read_text(encoding="utf-8"), "replaced\n")
        undone = qe.undo()
        self.assertEqual(undone["problems"], [])
        self.assertEqual((self.folder / "a.txt").read_text(encoding="utf-8"), "hello\nworld\n")
        self.assertTrue((self.folder / "b.txt").is_file())
        self.assertFalse((self.folder / "Texts").exists())
        self.assertFalse((self.folder / "new.md").exists())
        self.assertTrue((self.folder / "sub" / "c.csv").is_file())
        self.assertEqual((self.folder / "notes.md").read_text(encoding="utf-8"), "# Title\nsome foo text\n")
        with self.assertRaises(qe.FolderError):
            qe.undo(undone["id"])

    def test_changed_file_is_not_overwritten(self) -> None:
        change = qe.plan_changes(self.ctx, [{"op": "append", "path": "a.txt", "text": "more"}])
        time.sleep(0.01)
        (self.folder / "a.txt").write_text("someone else\n", encoding="utf-8")
        os.utime(self.folder / "a.txt", None)
        result = qe.apply_set(change["id"])
        self.assertEqual(result["failed"], 1)
        self.assertEqual((self.folder / "a.txt").read_text(encoding="utf-8"), "someone else\n")

    def test_conflicts_inside_a_set(self) -> None:
        change = qe.plan_changes(self.ctx, [{"op": "move", "path": "a.txt", "to": "c.txt"},
                                            {"op": "move", "path": "b.txt", "to": "c.txt"},
                                            {"op": "move", "path": "a.txt", "to": "b.txt"},
                                            {"op": "move", "path": "sub", "to": "sub/inner"}])
        self.assertEqual([op["status"] for op in change["ops"]], ["pending", "invalid", "invalid", "invalid"])

    def test_full_mode_applies_safe_ops_and_waits_for_risky(self) -> None:
        quick_apps.save_settings({"access": "full"})
        try:
            out = qe._change_tool(self.ctx, {"summary": "s", "ops": [{"op": "mkdir", "path": "Docs"},
                                                                     {"op": "delete", "path": "photo.jpg"}]}, "en")
        finally:
            quick_apps.save_settings({"access": "ask"})
        self.assertEqual((out["applied"], out["waiting"]), (1, 1))
        self.assertTrue((self.folder / "Docs").is_dir())
        self.assertTrue((self.folder / "photo.jpg").exists())
        self.assertEqual(out["ui"]["type"], "changeset")
        self.assertIn("NOT done", out["note"])

    def test_discard(self) -> None:
        change = qe.plan_changes(self.ctx, [{"op": "delete", "path": "a.txt"}])
        self.assertTrue(qe.discard_set(change["id"]))
        self.assertEqual(qe.apply_set(change["id"])["applied"], 0)
        self.assertTrue((self.folder / "a.txt").exists())

class CaptureTests(unittest.TestCase):
    def test_capture_explorer_with_fakes(self) -> None:
        folder = make_folder()
        describe = lambda hwnd: {"class": "CabinetWClass", "exe": r"C:\Windows\explorer.exe", "title": folder.name, "pid": 5,
                                 "color": {"hex": "#112233", "r": 17, "g": 34, "b": 51}}
        with mock.patch.object(quick_apps_win, "explorer_folder",
                               return_value={"path": str(folder), "selected": [str(folder / "a.txt")]}):
            ctx = quick_apps.capture(42, describe=describe, prefs=dict(quick_apps.DEFAULT_SETTINGS))
        self.assertEqual((ctx["kind"], ctx["folder"]), ("explorer", str(folder)))
        self.assertEqual(ctx["color"]["hex"], "#112233")
        with mock.patch.object(quick_apps_win, "explorer_folder", return_value={"path": "", "reason": "virtual"}):
            ctx = quick_apps.capture(42, describe=describe, prefs=dict(quick_apps.DEFAULT_SETTINGS))
        self.assertEqual((ctx["folder"], ctx["reason"]), ("", "virtual"))
        off = dict(quick_apps.DEFAULT_SETTINGS, explorer=False)
        self.assertEqual(quick_apps.capture(42, describe=describe, prefs=off)["reason"], "off")

    def test_note_foreground_in_background(self) -> None:
        folder = make_folder()
        with mock.patch.object(quick_apps_win, "describe_window",
                               return_value={"class": "CabinetWClass", "exe": "explorer.exe", "title": "x", "pid": 7}), \
                mock.patch.object(quick_apps_win, "explorer_folder", return_value={"path": str(folder), "selected": []}):
            quick_apps.note_foreground(99)
            ctx = quick_apps.current_context(3.0)
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["folder"], str(folder))
        self.assertTrue(ctx["writable"])
        public = quick_apps.public_context(ctx)
        self.assertNotIn("hwnd", public)
        self.assertNotIn("pid", public)

    def test_own_window_gives_no_context(self) -> None:
        with mock.patch.object(quick_apps_win, "describe_window",
                               return_value={"class": "x", "exe": "python.exe", "title": "MemuAI", "pid": os.getpid()}):
            quick_apps.note_foreground(5, own_pids=(os.getpid(),))
            self.assertIsNone(quick_apps.current_context(3.0))

class AskEventsTests(unittest.TestCase):
    def test_folder_tools_and_changeset_ui(self) -> None:
        folder = make_folder()
        ctx = ctx_for(folder)
        settings = dict(core.PREF_DEFAULTS, quick_ask_web_search=False)
        settings.update(qe.request_settings({"contexts": [{"type": "app", "id": ctx["id"]}]}, "en"))
        replies = [
            '```memuai-tool\n[{"tool":"folder_list","args":{}}]\n```',
            '```memuai-tool\n[{"tool":"folder_change","args":{"summary":"rename","ops":[{"op":"move","path":"a.txt","to":"A1.txt"}]}}]\n```',
            "I proposed one rename; press Apply.",
        ]
        requests = []

        async def stream_lines(path, payload):
            requests.append(json.loads(payload.decode("utf-8")))
            text = replies.pop(0)
            yield "data: " + json.dumps({"choices": [{"delta": {"content": text}}]})
            yield "data: [DONE]"

        async def collect():
            out = []
            async for chunk in quick_ask_api.ask_events(
                    text="rename a.txt", images=[], selection="", model="m", history=[], vision=True,
                    native_search=False, settings=settings, lang="en", stream_lines=stream_lines, run_tool=None,
                    rich_search=None, rich_payload=None, capture=None):
                out.append(json.loads(chunk[6:]))
            return out
        events = asyncio.run(collect())
        system = requests[0]["messages"][0]["content"]
        self.assertIn("folder_change", system)
        self.assertIn("<folder_context>", json.dumps(requests[0]["messages"], ensure_ascii=False))
        ui = [e for e in events if e.get("event") == "tool_ui"]
        self.assertEqual(ui[0]["ui"]["type"], "changeset")
        self.assertEqual(ui[0]["ui"]["set"]["ops"][0]["to"], "A1.txt")
        self.assertTrue((folder / "a.txt").exists())
        self.assertEqual(events[-1]["event"], "done")

    def test_tools_hidden_without_context(self) -> None:
        settings = dict(core.PREF_DEFAULTS)
        names = [line.split()[0] for line in quick_ask_api.extra_tool_lines(settings)]
        self.assertNotIn("folder_list", names)
        self.assertNotIn("folder_change", names)

class RoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.include_router(qe.router)
        app.include_router(quick_extras.router)
        self.client = TestClient(app)
        self.folder = make_folder()
        self.ctx = ctx_for(self.folder)

    def test_context_and_actions(self) -> None:
        data = self.client.get("/api/quick/apps/context?lang=en").json()
        self.assertEqual(data["context"]["id"], self.ctx["id"])
        self.assertIn("settings", data)
        info = self.client.get(f"/api/quick/explorer/info?id={self.ctx['id']}").json()
        self.assertEqual(info["files"], 5)
        self.assertEqual(self.client.get("/api/quick/explorer/info?id=bad").status_code, 400)
        found = self.client.post("/api/quick/explorer/search", json={"id": self.ctx["id"], "query": "foo"}).json()
        self.assertEqual(found["matches"][0]["path"], "notes.md")
        dups = self.client.post("/api/quick/explorer/duplicates", json={"id": self.ctx["id"]}).json()
        self.assertEqual(len(dups["groups"]), 1)
        made = self.client.post("/api/quick/explorer/changes", json={"id": self.ctx["id"], "ops": [{"op": "delete", "path": "b.txt"}]}).json()
        set_id = made["set"]["id"]
        self.assertEqual(self.client.get(f"/api/quick/explorer/changes/{set_id}").json()["set"]["risky"], 1)
        applied = self.client.post(f"/api/quick/explorer/changes/{set_id}/apply", json={}).json()
        self.assertEqual(applied["applied"], 1)
        self.assertFalse((self.folder / "b.txt").exists())
        undone = self.client.post("/api/quick/explorer/undo", json={}).json()
        self.assertEqual(undone["undone"], 1)
        self.assertTrue((self.folder / "b.txt").exists())
        self.assertEqual(self.client.post("/api/quick/explorer/changes/csnope/apply", json={}).status_code, 404)
        bad = self.client.post("/api/quick/apps/reveal", json={"id": self.ctx["id"], "path": "../x"})
        self.assertEqual(bad.status_code, 400)
        (self.folder / "run.bat").write_text("echo", encoding="utf-8")
        exe = self.client.post("/api/quick/apps/reveal", json={"id": self.ctx["id"], "path": "run.bat", "open": True})
        self.assertEqual(exe.json().get("code"), "executable")

    def test_settings_routes(self) -> None:
        saved = self.client.post("/api/quick/apps/settings", json={"settings": {"currency_to": "usd"}}).json()
        self.assertEqual(saved["settings"]["currency_to"], "USD")
        presets = self.client.post("/api/quick/apps/presets", json={"presets": [{"name": "N", "prompt": "P", "apps": ["x"]}]}).json()
        self.assertEqual(presets["presets"][0]["apps"], ["x.exe"])
        reset = self.client.post("/api/quick/apps/presets", json={"reset": True, "lang": "ru"}).json()
        self.assertGreaterEqual(len(reset["presets"]), 3)
        self.client.post("/api/quick/apps/settings", json={"settings": {"currency_to": ""}})

class ExtrasTests(unittest.TestCase):
    def test_rates(self) -> None:
        calls = []

        def fetch(url):
            calls.append(url)
            if "er-api" in url:
                raise OSError("down")
            return {"rates": {"EUR": "0.9", "RUB": 90, "bad": 1, "XXX": -1}}
        with mock.patch.object(quick_extras, "_rates_file", return_value=Path(tempfile.mkdtemp()) / "r.json"):
            data = quick_extras.load_rates("usd", fetch=fetch, now=1000)
            self.assertEqual(data["source"], "frankfurter.app")
            self.assertEqual(data["rates"]["USD"], 1.0)
            self.assertNotIn("XXX", data["rates"])
            again = quick_extras.load_rates("USD", fetch=lambda url: (_ for _ in ()).throw(AssertionError()), now=2000)
            self.assertEqual(again["rates"]["RUB"], 90)
            stale = quick_extras.load_rates("USD", fetch=lambda url: {}, now=1000 + quick_extras.RATES_TTL + 5)
            self.assertTrue(stale["stale"])
        self.assertAlmostEqual(quick_extras.convert(9, "EUR", "RUB", {"USD": 1, "EUR": 0.9, "RUB": 90}), 900)
        self.assertEqual(quick_extras.parse_rates("open.er-api.com", {"result": "error"}), {})

    def test_notes(self) -> None:
        folder = Path(tempfile.mkdtemp())
        settings = {"notes_dir": str(folder)}
        stamp = time.mktime((2026, 10, 9, 14, 5, 0, 0, 0, -1))
        r1 = quick_extras.add_note("first\nsecond line", settings=settings, now=stamp)
        r2 = quick_extras.add_note("third", settings=settings, now=stamp + 60)
        r3 = quick_extras.add_note("next day", settings=settings, now=stamp + 86400)
        self.assertEqual(r1["path"], r2["path"])
        text = Path(r1["path"]).read_text(encoding="utf-8")
        self.assertEqual(text, "# Быстрые заметки\n## 2026-10-09\n- 14:05 first\n  second line\n- 14:06 third\n\n## 2026-10-10\n- 14:05 next day\n")
        self.assertTrue(r3["ok"])
        self.assertIn("error", quick_extras.add_note("  ", settings=settings))

    def test_downloads(self) -> None:
        folder = Path(tempfile.mkdtemp())
        for i, name in enumerate(["old.pdf", "new.zip", "x.crdownload", "desktop.ini"]):
            (folder / name).write_text("x", encoding="utf-8")
            os.utime(folder / name, (1000 + i, 1000 + i))
        rows = quick_extras.latest_downloads(str(folder))
        self.assertEqual([r["name"] for r in rows], ["new.zip", "old.pdf"])
        with mock.patch.object(quick_apps, "downloads_folder", return_value=str(folder)):
            used = quick_extras.downloads_context("new.zip")
            self.assertEqual(used["context"]["selected"], ["new.zip"])
            self.assertIn("error", quick_extras.downloads_context("../etc/passwd"))

    def test_ocr_guards(self) -> None:
        self.assertEqual(quick_extras.ocr_image("nope")["code"], "bad_image")
        if os.name != "nt":
            self.assertEqual(quick_extras.ocr_image("data:image/png;base64,AAAA")["code"], "unsupported")

class FrontendTests(unittest.TestCase):
    def test_manifest_spec_build_docs(self) -> None:
        manifest = json.loads((PLUGINS / "index.json").read_text(encoding="utf-8"))
        rows = {row["id"]: row for row in manifest["plugins"]}
        self.assertEqual(rows["explorer"]["backend"], "app.quick_explorer")
        for row in (rows["explorer"], rows["extras"]):
            for key in ("src", "css", "settings", "settings_css"):
                if row.get(key):
                    self.assertTrue((PLUGINS / row[key]).is_file(), row[key])
        spec = (ROOT / "MemuAI.spec").read_text(encoding="utf-8")
        build = (ROOT / "build.py").read_text(encoding="utf-8")
        for mod in ("app.quick_explorer", "app.quick_apps", "app.quick_apps_win", "app.quick_extras"):
            self.assertIn(f"'{mod}'", spec)
            self.assertIn(f'"{mod}"', build)
        for lang in ("ru", "en"):
            self.assertTrue((ROOT / "app" / "docs" / lang / "quick-explorer.md").is_file())
        quick = (ROOT / "app" / "pgstatic" / "quick" / "quick.js").read_text(encoding="utf-8")
        self.assertIn("addRequestContext", quick)
        self.assertIn("contexts: selAction ? [] : collectContexts()", quick)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_js_syntax_and_currency_parser(self) -> None:
        for name in ("explorer.js", "explorer-settings.js", "extras.js"):
            subprocess.run(["node", "--check", str(PLUGINS / name)], check=True)
        script = ("const x=require(%s);const cases=%s;process.stdout.write(JSON.stringify(cases.map(c=>x.parseCurrency(c))))"
                  % (json.dumps(str(PLUGINS / "extras.js")), json.dumps(
                      ["100 usd в rub", "€50 to $", "курс доллара", "1 000 руб в долларах", "2.5k eur in usd",
                       "100 сумма", "5 km в милях", "hello", "10 usd в usd", "100"])))
        out = json.loads(subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True).stdout)
        self.assertEqual(out[0], {"amount": 100, "from": "USD", "to": "RUB"})
        self.assertEqual(out[1], {"amount": 50, "from": "EUR", "to": "USD"})
        self.assertEqual(out[2], {"amount": 1, "from": "USD", "to": None})
        self.assertEqual(out[3], {"amount": 1000, "from": "RUB", "to": "USD"})
        self.assertEqual(out[4], {"amount": 2500, "from": "EUR", "to": "USD"})
        self.assertEqual(out[5:], [None] * 5)

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_dom_smoke_with_jsdom(self) -> None:
        """The real Quick Ask page + plugins in jsdom (``MEMU_JSDOM`` = a node_modules folder with jsdom)."""
        env = dict(os.environ)
        env["NODE_PATH"] = os.environ.get("MEMU_JSDOM") or "/tmp/jsd/node_modules"
        probe = subprocess.run(["node", "-e", "require('jsdom')"], env=env, capture_output=True)
        if probe.returncode != 0:
            self.skipTest("jsdom is not installed (npm install jsdom; MEMU_JSDOM=<node_modules>)")
        for lang in ("ru", "en"):
            run = subprocess.run(["node", str(ROOT / "tests" / "round29_quick_explorer_dom.js"), lang], env=env,
                                 capture_output=True, text=True, timeout=90)
            self.assertEqual(run.stdout.strip(), "OK", run.stdout + run.stderr)

    def test_english_strings_have_no_cyrillic(self) -> None:
        import re
        pair = re.compile(r"\bt(?:r)?\(\s*(?:\"(?:[^\"\\]|\\.)*\"|`(?:[^`\\]|\\.)*`)\s*,\s*(\"(?:[^\"\\]|\\.)*\"|`(?:[^`\\]|\\.)*`)\s*\)")
        for name in ("explorer.js", "explorer-settings.js", "extras.js"):
            source = (PLUGINS / name).read_text(encoding="utf-8")
            for match in pair.finditer(source):
                english = match.group(1)
                self.assertIsNone(re.search(r"[А-Яа-яЁё]", english), f"{name}: {english[:80]}")

if __name__ == "__main__":
    unittest.main()
