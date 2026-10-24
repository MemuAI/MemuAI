"""Round 30 (r30-quick-panels, item 9): Quick Access does file tasks instead of printing a command to type.

Mock file system (a temporary HOME with Desktop / Downloads / Trash), the real change-set engine of
app/quick_explorer.py, and a mock model that answers with a ``memuai-tool`` call — the tool must be offered in the
system prompt and executed. No network.
"""
import asyncio
import json
import os
import shutil
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


class FsCase(unittest.TestCase):
    def setUp(self):
        from app import quick_apps
        self.tmp = Path(tempfile.mkdtemp(prefix="r30-files-"))
        self.home = self.tmp / "home"
        for name in ("Desktop", "Downloads", "Documents"):
            (self.home / name).mkdir(parents=True)
        env = {"HOME": str(self.home), "XDG_DATA_HOME": str(self.home / ".local" / "share"),
               "XDG_CONFIG_HOME": str(self.home / ".config"), "XDG_DESKTOP_DIR": str(self.home / "Desktop"),
               "XDG_DOWNLOAD_DIR": str(self.home / "Downloads"), "XDG_DOCUMENTS_DIR": str(self.home / "Documents"),
               "OneDrive": ""}
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        self.settings = dict(quick_apps.DEFAULT_SETTINGS)
        self.patch = mock.patch.object(quick_apps, "settings", side_effect=lambda: dict(self.settings))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)


class Resolve(FsCase):
    def test_places_absolute_current_and_refusals(self):
        from app import quick_files as qf
        desk = self.home / "Desktop"
        self.assertEqual(qf.resolve("Desktop/Отчёты"), desk / "Отчёты")
        self.assertEqual(qf.resolve("рабочий стол/a.txt"), desk / "a.txt")
        self.assertEqual(qf.resolve("Загрузки"), self.home / "Downloads")
        self.assertEqual(qf.resolve("~/Documents/x"), self.home / "Documents" / "x")
        self.assertEqual(qf.resolve(str(desk / "y")), desk / "y")
        ctx = {"folder": str(self.home / "Downloads")}
        self.assertEqual(qf.resolve("./z.zip", ctx), self.home / "Downloads" / "z.zip")
        self.assertEqual(qf.resolve("z.zip", ctx), self.home / "Downloads" / "z.zip")
        for bad, code in (("Desktop/../..", "bad_path"), ("notes.txt", "unclear"), ("./a", "no_folder"), ("", "empty")):
            with self.subTest(bad=bad), self.assertRaises(qf.FilesError) as err:
                qf.resolve(bad)
            self.assertEqual(err.exception.code, code)

    def test_memuai_own_folders_are_off_limits(self):
        from app import quick_files as qf
        from app.pgdata import MEMU_DIR
        for path in (Path(MEMU_DIR) / "x.json", Path(qf.__file__).resolve(), self.home / ".ssh" / "id_rsa"):
            with self.subTest(path=path), self.assertRaises(qf.FilesError) as err:
                qf.resolve(str(path))
            self.assertEqual(err.exception.code, "forbidden")

    def test_places_list_and_labels(self):
        from app import quick_files as qf
        keys = {row["key"]: row for row in qf.places("ru")}
        self.assertEqual(keys["desktop"]["path"], str(self.home / "Desktop"))
        self.assertEqual(keys["desktop"]["label"], "Рабочий стол")
        self.assertEqual(qf.label_for(self.home / "Downloads" / "Фото", "ru"), "Загрузки/Фото")
        self.assertEqual(qf.label_for(self.home / "Desktop", "en"), "Desktop")


class Changes(FsCase):
    def test_mkdir_on_desktop_full_is_done_at_once_and_undone(self):
        from app import quick_files as qf, quick_explorer as qe
        self.settings["access"] = "full"
        out = qf.tool_change(None, {"summary": "Новая папка", "ops": [{"op": "mkdir", "path": "Desktop/Проекты"}]}, "ru")
        self.assertEqual(out["applied"], 1)
        self.assertTrue((self.home / "Desktop" / "Проекты").is_dir())
        self.assertEqual(out["ui"]["type"], "changeset")
        self.assertEqual(out["ui"]["set"]["folder_name"], "Рабочий стол")
        qe.undo(out["ui"]["set"]["undo"])
        self.assertFalse((self.home / "Desktop" / "Проекты").exists())

    def test_ask_mode_waits_for_apply(self):
        from app import quick_files as qf, quick_explorer as qe
        out = qf.tool_change(None, {"ops": [{"op": "mkdir", "path": "Desktop/A"}]}, "en")
        self.assertEqual((out["applied"], out["waiting"]), (0, 1))
        self.assertIn("NOT done yet", out["note"])
        self.assertFalse((self.home / "Desktop" / "A").exists())
        qe.apply_set(out["set"])
        self.assertTrue((self.home / "Desktop" / "A").is_dir())

    def test_unnamed_folder_is_numbered_like_explorer(self):
        from app import quick_files as qf
        self.settings["access"] = "full"
        (self.home / "Desktop" / "Новая папка").mkdir()
        out = qf.tool_change(None, {"ops": [{"op": "mkdir", "path": "Desktop/Новая папка"},
                                            {"op": "mkdir", "path": "Desktop/Новая папка"}]}, "ru")
        self.assertEqual(out["applied"], 2)
        self.assertTrue((self.home / "Desktop" / "Новая папка (2)").is_dir())
        self.assertTrue((self.home / "Desktop" / "Новая папка (3)").is_dir())

    def test_move_between_places_and_rename_in_explorer_folder(self):
        from app import quick_files as qf, quick_apps
        self.settings["access"] = "full"
        (self.home / "Downloads" / "report.pdf").write_bytes(b"%PDF")
        out = qf.tool_change(None, {"ops": [{"op": "move", "path": "Downloads/report.pdf", "to": "Desktop/report.pdf"}]}, "ru")
        self.assertEqual(out["applied"], 1)
        self.assertTrue((self.home / "Desktop" / "report.pdf").is_file())
        ctx = quick_apps.register_context({"kind": "explorer", "folder": str(self.home / "Desktop")}, current=False)
        out = qf.tool_change(ctx, {"ops": [{"op": "move", "path": "./report.pdf", "to": "./Отчёт 2026.pdf"}]}, "ru")
        self.assertEqual(out["applied"], 1)
        self.assertTrue((self.home / "Desktop" / "Отчёт 2026.pdf").is_file())

    def test_move_to_a_folder_name_means_into_it(self):
        from app import quick_files as qf
        self.settings["access"] = "full"
        down = self.home / "Downloads"
        for name in ("a.jpg", "b.docx"):
            (down / name).write_bytes(b"1")
        (down / "Старое").mkdir()
        out = qf.tool_change(None, {"ops": [{"op": "mkdir", "path": "Downloads/Изображения"},
                                            {"op": "move", "path": "Downloads/a.jpg", "to": "Downloads/Изображения"},
                                            {"op": "move", "path": "Downloads/b.docx", "to": "Downloads/Старое"}]}, "ru")
        self.assertEqual(out["applied"], 3)
        self.assertTrue((down / "Изображения" / "a.jpg").is_file())
        self.assertTrue((down / "Старое" / "b.docx").is_file())

    def test_bare_json_tool_call_is_run(self):
        from app import quick_ask_api, quick_ask_core as core, quick_explorer, quick_files
        quick_files.register_tools()
        self.settings["access"] = "full"
        replies = ['[{"tool":"files_change","args":{"ops":[{"op":"mkdir","path":"Desktop/X"}]}}]', "Готово."]

        async def stream_lines(path, payload):
            yield "data: " + json.dumps({"choices": [{"delta": {"content": replies.pop(0)}}]}, ensure_ascii=False)
        settings = dict(core.PREF_DEFAULTS, quick_ask_web_search=False)
        settings.update(quick_explorer.request_settings({"text": "создай папку X на рабочем столе"}, "ru"))

        async def collect():
            return [json.loads(c[6:]) async for c in quick_ask_api.ask_events(
                text="создай папку X на рабочем столе", images=[], selection="", model="m", history=[], vision=False,
                native_search=False, settings=settings, lang="ru", stream_lines=stream_lines, run_tool=None,
                rich_search=None, rich_payload=None, capture=None)]
        events = asyncio.run(collect())
        self.assertTrue((self.home / "Desktop" / "X").is_dir())
        self.assertEqual(events[-1]["text"], "Готово.")

    def test_system_folders_and_cross_drive_are_refused(self):
        from app import quick_files as qf
        with self.assertRaises(qf.FilesError) as err:
            qf.tool_change(None, {"ops": [{"op": "mkdir", "path": "/etc/memu-test"}]}, "en")
        self.assertEqual(err.exception.code, "read_only")

    def test_delete_goes_to_the_trash_after_the_click_and_undo_restores(self):
        from app import quick_files as qf, quick_explorer as qe
        self.settings["access"] = "full"
        victim = self.home / "Desktop" / "old.txt"
        victim.write_text("bye", encoding="utf-8")
        out = qf.tool_change(None, {"ops": [{"op": "delete", "path": "Desktop/old.txt"}]}, "ru")
        self.assertEqual((out["applied"], out["waiting"]), (0, 1))     # a delete always waits for the user
        self.assertTrue(out["ui"]["set"]["trash"])
        self.assertTrue(victim.exists())
        result = qe.apply_set(out["set"])
        self.assertEqual(result["applied"], 1)
        self.assertFalse(victim.exists())
        trash = self.home / ".local" / "share" / "Trash"
        self.assertEqual((trash / "files" / "old.txt").read_text(encoding="utf-8"), "bye")
        info = (trash / "info" / "old.txt.trashinfo").read_text(encoding="utf-8")
        self.assertIn("Path=" + str(victim).replace(" ", "%20"), info)
        qe.undo(result["set"]["undo"])
        self.assertEqual(victim.read_text(encoding="utf-8"), "bye")
        self.assertFalse((trash / "info" / "old.txt.trashinfo").exists())

    def test_trash_off_uses_the_undo_stash(self):
        from app import quick_files as qf, quick_explorer as qe
        self.settings.update(access="full", trash=False)
        victim = self.home / "Desktop" / "x.txt"
        victim.write_text("x", encoding="utf-8")
        out = qf.tool_change(None, {"ops": [{"op": "delete", "path": str(victim)}]}, "ru")
        self.assertFalse(out["ui"]["set"]["trash"])
        result = qe.apply_set(out["set"])
        self.assertFalse(victim.exists())
        self.assertFalse((self.home / ".local" / "share" / "Trash" / "files" / "x.txt").exists())
        qe.undo(result["set"]["undo"])
        self.assertTrue(victim.exists())

    def test_list_find_open(self):
        from app import quick_files as qf
        (self.home / "Downloads" / "Счёт март.pdf").write_bytes(b"1")
        (self.home / "Documents" / "notes.md").write_text("пароль от wifi: нет", encoding="utf-8")
        listed = qf.tool_list(None, {"path": "Downloads"}, "ru")
        self.assertEqual([row["path"] for row in listed["entries"]], ["Счёт март.pdf"])
        found = qf.tool_find(None, {"query": "счёт"}, "ru")
        self.assertEqual([Path(row["path"]).name for row in found["found"]], ["Счёт март.pdf"])
        found = qf.tool_find(None, {"query": "wifi", "content": True}, "ru")
        self.assertEqual([Path(row["path"]).name for row in found["content_matches"]], ["notes.md"])
        with mock.patch.object(qf, "open_path", return_value=True) as opener:
            self.assertTrue(qf.tool_open(None, {"path": "Downloads/Счёт март.pdf"}, "ru")["ok"])
            opener.assert_called_once_with(str(self.home / "Downloads" / "Счёт март.pdf"), False)
        (self.home / "Downloads" / "setup.exe").write_bytes(b"MZ")
        with self.assertRaises(qf.FilesError):
            qf.tool_open(None, {"path": "Downloads/setup.exe"}, "ru")


class Trash(unittest.TestCase):
    def test_recycle_bin_index_parser_and_lookup(self):
        from app import quick_trash
        path = "C:\\Users\\me\\Desktop\\old.txt"
        raw = path + "\x00"
        data = struct.pack("<qqqi", 2, 3, 133500000000000000, len(raw)) + raw.encode("utf-16-le")
        info = quick_trash.parse_index(data)
        self.assertEqual((info["path"], info["size"]), (path, 3))
        self.assertGreater(info["deleted"], 1.6e9)
        v1 = struct.pack("<qqq", 1, 5, 0) + raw.encode("utf-16-le").ljust(520, b"\x00")
        self.assertEqual(quick_trash.parse_index(v1)["path"], path)
        self.assertIsNone(quick_trash.parse_index(b"\x07" + b"\x00" * 40))
        with tempfile.TemporaryDirectory() as tmp:
            sid = Path(tmp)
            (sid / "$IAB12CD.txt").write_bytes(data)
            (sid / "$RAB12CD.txt").write_text("bye")
            found = quick_trash.find_recycled(path, time.time() - 1, dirs=[sid])
            if os.name != "nt":   # normcase is a no-op off Windows: the stored path is compared as is
                self.assertEqual(Path(found["item"]).name, "$RAB12CD.txt")
            self.assertIsNone(quick_trash.find_recycled("C:\\other.txt", time.time() - 1, dirs=[sid]))


class RequestHook(FsCase):
    def test_file_questions_get_the_tools_and_others_do_not(self):
        from app import quick_explorer
        out = quick_explorer.request_settings({"text": "создай папку на рабочем столе"}, "ru")
        self.assertTrue(out["_quick_files"])
        self.assertIn("<files>", out["_quick_context"])
        self.assertIn(str(self.home / "Desktop"), out["_quick_context"])
        self.assertIn("never answer with a command", out["_quick_context"])
        self.assertGreaterEqual(out["_quick_tool_rounds"], 8)
        for text in ("какая погода в Казани", "переведи hello", "сколько будет 2+2"):
            with self.subTest(text=text):
                self.assertEqual(quick_explorer.request_settings({"text": text}, "ru"), {})
        self.settings["files"] = False
        self.assertEqual(quick_explorer.request_settings({"text": "создай папку на рабочем столе"}, "ru"), {})

    def test_question_sent_while_the_context_is_read_waits_for_it(self):
        from app import quick_apps, quick_explorer
        folder = self.home / "Downloads"
        with quick_apps._LOCK:
            quick_apps._STATE["event"].clear()
            quick_apps._STATE["current"] = None

        def late():
            time.sleep(0.3)
            quick_apps.register_context({"kind": "explorer", "app": "Explorer", "folder": str(folder)})
            quick_apps._STATE["event"].set()
        threading.Thread(target=late, daemon=True).start()
        started = time.monotonic()
        out = quick_explorer.request_settings({"text": "переименуй файлы в этой папке",
                                               "contexts": [{"type": "app", "pending": True}]}, "ru")
        self.assertLess(time.monotonic() - started, 2.5)
        self.assertIn("<folder_context>", out["_quick_context"])
        self.assertIn(str(folder), out["_quick_context"])
        self.assertTrue(out["_quick_files"])

    def test_page_marks_the_context_pending_while_loading(self):
        js = (Path(__file__).resolve().parents[1] / "app" / "pgstatic" / "quick" / "plugins" / "explorer.js").read_text("utf-8")
        self.assertIn('S.loading && !S.ctx ? { type: "app", pending: true }', js)


class ModelGetsAndRunsTheTools(FsCase):
    """The mock provider answers with a files_change tool call: the tool is in the prompt, it runs, the card shows."""

    def test_tool_call_creates_the_folder(self):
        from app import quick_ask_api, quick_ask_core as core, quick_explorer, quick_files
        quick_files.register_tools()
        self.settings["access"] = "full"
        call = ('```memuai-tool\n[{"tool":"files_change","args":{"summary":"Папка на рабочем столе","ops":'
                '[{"op":"mkdir","path":"Desktop/Новая папка"}]}}]\n```')
        replies = [call, "Готово: на рабочем столе создана «Новая папка»."]
        prompts = []

        async def stream_lines(path, payload):
            prompts.append(json.loads(payload)["messages"])
            yield "data: " + json.dumps({"choices": [{"delta": {"content": replies.pop(0)}}]}, ensure_ascii=False)
            yield "data: [DONE]"

        async def no_search(*a, **k):
            raise AssertionError("no web pre-search for a file task")
        text = "создай папку на рабочем столе"
        settings = dict(core.PREF_DEFAULTS, quick_ask_web_search=True, web_search_mode="auto")
        settings.update(quick_explorer.request_settings({"text": text}, "ru"))

        async def collect():
            return [json.loads(chunk[6:]) async for chunk in quick_ask_api.ask_events(
                text=text, images=[], selection="", model="mock", history=[], vision=False, native_search=False,
                settings=settings, lang="ru", stream_lines=stream_lines, run_tool=None, rich_search=no_search,
                rich_payload=None, capture=None)]
        events = asyncio.run(collect())
        system = prompts[0][0]["content"]
        self.assertIn("files_change", system)
        self.assertIn("files_open", system)
        self.assertTrue((self.home / "Desktop" / "Новая папка").is_dir())
        ui = [e for e in events if e["event"] == "tool_ui"]
        self.assertEqual(ui[0]["ui"]["type"], "changeset")
        self.assertEqual(events[-1]["event"], "done")
        self.assertIn("Новая папка", events[-1]["text"])
        tool_result = prompts[1][-1]["content"]
        self.assertIn('"applied": 1', tool_result)

    def test_tools_are_hidden_for_other_questions(self):
        from app import quick_ask_api, quick_files
        quick_files.register_tools()
        lines = quick_ask_api.extra_tool_lines({})
        self.assertFalse([line for line in lines if line.startswith("files_")])
        lines = quick_ask_api.extra_tool_lines({"_quick_files": True})
        self.assertEqual(len([line for line in lines if line.startswith("files_")]), 4)


class AssistantToolLine(unittest.TestCase):
    def test_shell_is_listed_only_with_the_advanced_mode(self):
        from app import assistant_commands as ac, quick_ask_api
        eng = mock.Mock()
        state = {"advanced_shell": False}
        eng.settings.side_effect = lambda: ac.normalize_settings(dict(state))
        saved = quick_ask_api.EXTRA_TOOLS.get(ac.TOOL_NAME)
        try:
            self.assertTrue(ac.register_quick_tools(eng))
            line = lambda: [l for l in quick_ask_api.extra_tool_lines({}) if l.startswith(ac.TOOL_NAME)][0]
            self.assertNotIn("shell.run", line())
            state["advanced_shell"] = True
            self.assertIn("shell.run", line())
        finally:
            if saved is not None:
                quick_ask_api.EXTRA_TOOLS[ac.TOOL_NAME] = saved


if __name__ == "__main__":
    unittest.main()
