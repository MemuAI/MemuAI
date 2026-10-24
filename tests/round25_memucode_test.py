"""Round 25: MemuCode CLI — edits keep the file's indentation style, Read prefixes never land in a file, a typed
absolute path is a message (not a slash command), the completion toast does not depend on the OS check here, and
"git branch NAME" is not a read-only command in the web chat's Ask mode."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.cli.tools.files import apply_edit


class EditIndentation(unittest.TestCase):
    def test_spaces_from_the_model_become_tabs_in_a_tab_indented_file(self):
        text = "def f():\n\tif x:\n\t\treturn 1\n"
        result, count, fuzzy = apply_edit(text, "    if x:\n        return 1", "    if y:\n        return 2")
        self.assertEqual(result, "def f():\n\tif y:\n\t\treturn 2\n")
        self.assertEqual((count, fuzzy), (1, True))

    def test_tabs_from_the_model_become_spaces_in_a_space_indented_file(self):
        text = "def f():\n    if x:\n        return 1\n"
        result, _count, _fuzzy = apply_edit(text, "\tif x:\n\t\treturn 1", "\tif y:\n\t\treturn 2")
        self.assertEqual(result, "def f():\n    if y:\n        return 2\n")

    def test_a_deeper_tab_level_and_a_dropped_indent_both_land_in_tabs(self):
        text = "class A:\n\tdef f():\n\t\tif x:\n\t\t\treturn 1\n"
        nested = apply_edit(text, "    if x:\n        return 1", "    if y:\n        return 2")[0]
        flat = apply_edit(text, "if x:\n    return 1", "if y:\n    return 2")[0]
        expected = "class A:\n\tdef f():\n\t\tif y:\n\t\t\treturn 2\n"
        self.assertEqual(nested, expected)
        self.assertEqual(flat, expected)

    def test_a_top_level_match_in_go_or_a_makefile_still_gets_tabs(self):
        go = apply_edit("func f() {\n\treturn 1\n}\n", "func f() {\n    return 1\n}", "func f() {\n    return 2\n}")[0]
        make = apply_edit("all:\n\tgcc a.c\n", "all:\n    gcc a.c", "all:\n    gcc b.c")[0]
        self.assertEqual(go, "func f() {\n\treturn 2\n}\n")
        self.assertEqual(make, "all:\n\tgcc b.c\n")

    def test_two_space_files_keep_two_spaces(self):
        text = "class A:\n  def f():\n    if x:\n      return 1\n"
        result = apply_edit(text, "if x:\n  return 1", "if y:\n  return 2")[0]
        self.assertEqual(result, "class A:\n  def f():\n    if y:\n      return 2\n")

    def test_an_exact_match_is_left_untouched(self):
        text = "a\n\tb = 1\n"
        self.assertEqual(apply_edit(text, "b = 1", "b = 2"), ("a\n\tb = 2\n", 1, False))


class EditLineNumbers(unittest.TestCase):
    def test_a_line_added_without_a_prefix_does_not_bring_the_prefixes_into_the_file(self):
        text = "x = 1\ny = 2\n"
        result, _count, fuzzy = apply_edit(text, "     1\tx = 1\n     2\ty = 2",
                                           "     1\tx = 10\nz = 3\n     2\ty = 2")
        self.assertEqual(result, "x = 10\nz = 3\ny = 2\n")
        self.assertTrue(fuzzy)


class PatchCalls(unittest.TestCase):
    def test_an_apply_patch_call_is_told_how_to_edit_instead_of_missing_arguments(self):
        from app.cli.tools.base import ToolError
        from app.cli.tools.files import EditTool
        tool = EditTool()
        for body in ("*** Begin Patch\n*** Update File: a.py\n@@\n-x = 1\n+x = 2\n*** End Patch",
                      "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"):
            with self.assertRaises(ToolError) as caught:
                tool.check_required({"input": body})
            self.assertIn("NOT executed", str(caught.exception))
            self.assertIn("old_string", str(caught.exception))
        with self.assertRaises(ToolError) as caught:
            tool.check_required({"path": "a.py"})
        self.assertIn("missing required argument", str(caught.exception))
        args = {"path": "a.py", "old_string": "x", "new_string": "y"}
        tool.check_required(args)
        self.assertEqual(args["file_path"], "a.py")


class TypedPaths(unittest.TestCase):
    def test_an_existing_absolute_path_is_a_message_and_commands_stay_commands(self):
        from app.cli.tui.app import _is_command
        folder = Path(tempfile.mkdtemp(prefix="memucode-r25-"))
        big = folder / "big.txt"
        big.write_text("y", encoding="utf-8")
        self.assertTrue(_is_command("/help"))
        self.assertTrue(_is_command("/cd " + str(folder)))
        self.assertTrue(_is_command("/no/such/place here"))       # unknown: the command parser says so
        self.assertFalse(_is_command("//not a command"))
        self.assertFalse(_is_command("plain text"))
        if big.as_posix().startswith("/"):
            self.assertFalse(_is_command(big.as_posix()))
            self.assertFalse(_is_command(big.as_posix() + " what is in it?"))


class CompletionToast(unittest.TestCase):
    def test_the_toast_is_left_to_show_completion_which_checks_the_os_itself(self):
        from app.cli.tui.app import TerminalUI

        class Settings:
            def __init__(self, mode):
                self.mode = mode

            def get(self, key):
                return self.mode

        ui = TerminalUI.__new__(TerminalUI)
        ui.busy = False
        rings = []
        ui._ring = lambda: rings.append(1)
        with patch("app.desktop_notifications.show_completion") as toast, \
                patch("threading.Thread") as thread:
            thread.return_value.start = lambda: thread.call_args.kwargs["target"](*thread.call_args.kwargs["args"])
            for mode in ("auto", "bell", "off"):
                ui.settings = Settings(mode)
                ui._notify("Done", "text")
        self.assertEqual(toast.call_count, 1)
        self.assertEqual(rings, [1])


class WebCommandOutput(unittest.TestCase):
    """The MemuCode panel's commands (code_run, terminal, background tasks) read cp866/cp1251 like the CLI."""

    def test_a_legacy_code_page_line_and_crlf_are_read_like_in_the_cli(self):
        import io
        from app import code_sandbox
        raw = "ok\r\n".encode() + "Ошибка сборки\r\n".encode("cp866")
        with patch.object(code_sandbox, "legacy_codecs", lambda: ["cp866", "cp1251"]):
            self.assertEqual(code_sandbox._drain(io.BytesIO(raw)), "ok\nОшибка сборки\n")

    @unittest.skipIf(sys.platform == "win32", "POSIX shell")
    def test_a_background_task_keeps_utf8_and_reads_bytes(self):
        import time as _time
        from app import code_sandbox, code_sessions
        root = Path(tempfile.mkdtemp(prefix="memucode-r25-task-"))
        manager = code_sessions.TaskManager()
        command = "printf 'Привет\\r\\n'"
        row = manager.submit("r25-task", root, command, ["/bin/sh", "-c", command],
                             code_sandbox.filtered_env("r25-task", root), timeout=20)
        deadline = _time.time() + 10
        status = manager.status(row["id"])
        while (status["running"] or status["exit_code"] is None) and _time.time() < deadline:
            _time.sleep(0.05)
            status = manager.status(row["id"])
        _time.sleep(0.2)
        status = manager.status(row["id"])
        manager.close("r25-task")
        self.assertEqual(status["exit_code"], 0)
        self.assertEqual(status["stdout"], "Привет\n")


class WebIndexJunctions(unittest.TestCase):
    def test_a_junction_like_folder_is_not_walked(self):
        from app import code_context
        root = Path(tempfile.mkdtemp(prefix="memucode-r25-index-"))
        (root / "src").mkdir()
        (root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
        (root / "junction").mkdir()                        # stands for a Windows junction: a plain folder to os.walk
        (root / "junction" / "far.txt").write_text("far\n", encoding="utf-8")
        walked = []
        real = code_context.is_link

        def is_link(path):
            walked.append(path.name)
            return path.name == "junction" or real(path)

        with patch.object(code_context, "is_link", is_link):
            engine = code_context.CodeContextEngine(root)
            engine.refresh()
        self.assertIn("junction", walked)
        self.assertEqual(engine.tree, ["src/app.py"])


class AskModeGit(unittest.TestCase):
    def test_creating_a_branch_is_not_read_only(self):
        from app.code_tools import is_read_only
        self.assertTrue(is_read_only("git branch"))
        self.assertTrue(is_read_only("git branch -a"))
        self.assertTrue(is_read_only("git branch --list feat*"))
        self.assertTrue(is_read_only("git branch --contains abc123"))
        self.assertFalse(is_read_only("git branch feature"))
        self.assertFalse(is_read_only("git branch -D old"))


if __name__ == "__main__":
    unittest.main()


class RemoteBangCommandIsRefused(unittest.TestCase):
    """«!команда» с телефона не должна обходить диалог разрешений (Round 25)."""

    def test_bang_line_from_mini_app_is_not_dispatched(self):
        from unittest import mock
        from app.cli import remote
        ui = mock.MagicMock()
        ui.busy = False
        control = remote.RemoteControl.__new__(remote.RemoteControl)
        control.ui = ui
        control._submit("!rm -rf ~")
        ui.dispatch.assert_not_called()
        ui.say.assert_called()
        control._submit("покажи файлы")
        ui.dispatch.assert_called_once_with("покажи файлы")
