"""Focused input, transcript and session regressions for MemuCode CLI."""
from __future__ import annotations

import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from prompt_toolkit.document import Document

from app.cli.i18n import set_language, tr
from app.cli.tui.input import Attachments, CommandItem, MemuCompleter, pasted_paths

class InputFeatures(unittest.TestCase):
    def test_delete_selection_and_attachment_are_atomic(self):
        from prompt_toolkit.buffer import Buffer
        from app.cli.tui.app import TerminalUI
        buffer = Buffer(document=Document("abc", 0))
        buffer.start_selection()
        buffer.cursor_position = 3
        TerminalUI._delete_input(buffer, backward=True)
        self.assertEqual(buffer.text, "")
        for backward, position in ((True, 10), (False, 0)):
            buffer.document = Document("[Image #1] rest", position)
            TerminalUI._delete_input(buffer, backward=backward)
            self.assertEqual(buffer.text, " rest")
        buffer.reset()
        TerminalUI._delete_input(buffer, backward=True)
        TerminalUI._delete_input(buffer, backward=False)
        self.assertEqual(buffer.text, "")

    def test_only_supported_command_arguments_are_suggested(self):
        completer = MemuCompleter(lambda: [], lambda: [], lambda: [])
        self.assertEqual([c.text for c in completer.get_completions(Document('/cost '), None)],
                         ['set', 'refresh', 'prices'])
        for command in ('usage', 'sandbox', 'mcp'):
            self.assertEqual(list(completer.get_completions(Document('/' + command + ' '), None)), [])

        modes = [c.text for c in completer.get_completions(Document('/permissions '), None)]
        self.assertEqual(modes, ['default', 'acceptEdits', 'plan', 'auto', 'bypassPermissions'])

    def test_dialog_background_overrides_library_blue(self):
        from prompt_toolkit.styles import merge_styles
        from prompt_toolkit.styles.defaults import default_ui_style
        from app.cli.tui.theme import THEMES, pt_style
        for palette in THEMES.values():
            style = merge_styles([default_ui_style(), pt_style(palette)])
            for name in ('dialog.border', 'dialog.option', 'dialog.hint'):
                attrs = style.get_attrs_for_style_str('class:' + name)
                self.assertNotIn(attrs.bgcolor, ('ansiblue', '0000ff'))

    def test_command_arguments_and_directory_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "source files").mkdir()
            completer = MemuCompleter(lambda: [CommandItem("cd", "Change directory", "<path>")],
                                      lambda: [], lambda: [], lambda: root)
            names = [item.text for item in completer.get_completions(Document("/cd "), None)]
            self.assertTrue(any("source files" in item for item in names))
            self.assertTrue(any(item.startswith('"') for item in names))
            goals = [item.text for item in completer.get_completions(Document("/goal pa"), None)]
            self.assertEqual(goals, ["pause"])

    def test_drop_text_file_becomes_a_named_attachment(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "a file.txt"
            path.write_text("hello", encoding="utf-8")
            self.assertEqual(pasted_paths(f'"{path}"'), [str(path)])
            self.assertEqual(pasted_paths(str(path)), [str(path)])
            self.assertEqual(pasted_paths("x" * 5000), [])
            attachments = Attachments()
            token = attachments.file_token(path)
            self.assertEqual(token, "[File #1: a file.txt]")
            expanded, images = attachments.expand("Read " + token)
            self.assertIn("hello", expanded)
            self.assertEqual(images, [])

    def test_interface_language_is_always_english(self):
        self.assertEqual(set_language("ru"), "en")
        self.assertEqual(tr("Привет", "Hello"), "Hello")

class SessionFeatures(unittest.TestCase):
    def test_empty_session_uid_can_be_resumed_after_exit(self):
        from app.cli.session import Session
        from app.cli.main import SessionNotFound, _session_for, build_parser
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"MEMUCODE_CONFIG_DIR": folder}):
            root = Path(folder) / "project"
            root.mkdir()
            session = Session(root)
            session.ensure_saved()
            resumed = Session.load(root, session.id)
            self.assertEqual(resumed.id, session.id)
            self.assertEqual(resumed.messages, [])
            other = Path(folder) / "other"
            other.mkdir()
            selected, was_resumed = _session_for(other, build_parser().parse_args(["--resume", session.id]))
            self.assertTrue(was_resumed)
            self.assertEqual(selected.root, root)
            with self.assertRaises(SessionNotFound):
                _session_for(other, build_parser().parse_args(["--resume", "missing-id"]))

if __name__ == "__main__":
    unittest.main()
