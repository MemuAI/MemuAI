"""The full-screen interface driven through a pipe (no real console): pastes, completion, folding,
the status-line metric, clipboard shortcuts, the connecting state and the dialog guard."""
from __future__ import annotations

import asyncio
import io
import json
import re
import sys
import time
import unittest
from unittest.mock import patch

from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output.vt100 import Vt100_Output

from tests.memucode_cli_test import make_runtime, setUpModule, tearDownModule  # noqa: F401  (module fixtures)
from tests.memucode_transcript_test import texts

class Sink(io.StringIO):
    def isatty(self):
        return True

def drive_ui(runtime, drive, *, rows=40, columns=110):
    """Run the interface with a pipe as the keyboard; ``drive(ui, keys)`` plays the user."""
    from app.cli.tui.app import TerminalUI

    async def main():
        runtime.settings.overrides["onboarded"] = True
        sink = Sink()
        original = sys.stdout
        sys.stdout = sink
        try:
            ui = TerminalUI(runtime)
            output = Vt100_Output(sink, lambda: Size(rows=rows, columns=columns), term="xterm-256color", enable_cpr=False)
            with create_pipe_input() as keys:
                async def player():
                    try:
                        await asyncio.sleep(0.15)
                        return await drive(ui, keys)
                    finally:
                        ui.exit()
                task = asyncio.ensure_future(player())
                await ui.run(input=keys, output=output)
                outcome = await task
            await runtime.shutdown()
            return ui, outcome
        finally:
            sys.stdout = original

    return asyncio.run(main())

async def settle(seconds=0.25):
    await asyncio.sleep(seconds)

class Pasting(unittest.TestCase):
    def test_a_big_paste_is_one_chip_and_the_model_receives_all_of_it(self):
        big = "\n".join(f"line {index}: some text to paste" for index in range(40))
        runtime, backend = make_runtime(turns=["Received."])

        async def drive(ui, keys):
            keys.send_text("\x1b[200~" + big + "\x1b[201~")
            await settle()
            chip = ui.input_buffer.text
            keys.send_text("\r")
            for _ in range(60):
                await asyncio.sleep(0.05)
                if not ui.busy and backend.requests:
                    break
            return chip

        ui, chip = drive_ui(runtime, drive)
        self.assertRegex(chip, r"^\[Pasted text #1 \+40 lines\]$")
        sent = json.dumps(backend.requests[0].messages, ensure_ascii=False)
        self.assertIn("line 0: some text to paste", sent)
        self.assertIn("line 39: some text to paste", sent)

        ui.verbose = True
        shown = "\n".join(texts(ui.transcript.viewport(200, 110)))
        self.assertNotIn("[Pasted text", shown)
        self.assertIn("line 0: some text to paste", shown)
        self.assertIn("line 39: some text to paste", shown)

    def test_short_pastes_stay_plain_text_and_trailing_newlines_are_trimmed(self):
        runtime, _ = make_runtime()

        async def drive(ui, keys):
            keys.send_text("\x1b[200~hello\nworld\n\x1b[201~")
            await settle()
            return ui.input_buffer.text

        _ui, text = drive_ui(runtime, drive)
        self.assertEqual(text, "hello\nworld")

    def test_clicking_the_chip_edits_the_pasted_text(self):
        big = "\n".join(f"row {index} " + "x" * 30 for index in range(30))
        runtime, _ = make_runtime()

        async def drive(ui, keys):
            keys.send_text("\x1b[200~" + big + "\x1b[201~")
            await settle()
            ui.input_buffer.cursor_position = 3
            ui._edit_clicked_paste()
            await settle()
            editing = bool(ui.dialogs and ui.dialogs[0].tag == "paste")
            ui.dialog_text.insert_text("EDITED\n")
            keys.send_text("\x13")
            await settle()
            return editing, ui.attachments.pastes[1][:7], ui.input_buffer.text

        _ui, (editing, start, chip) = drive_ui(runtime, drive)
        self.assertTrue(editing)
        self.assertRegex(chip, r"\[Pasted text #1 \+\d+ lines\]")

class Completion(unittest.TestCase):
    def test_suggestions_survive_erasing_a_typo_with_backspace(self):
        runtime, _ = make_runtime()

        async def drive(ui, keys):
            keys.send_text("/lu")
            await settle(0.4)
            keys.send_text("\x7f")
            await settle(0.4)
            names = [completion.text for completion in ui.menu.items] if ui.menu else []
            keys.send_text("o")
            await settle(0.4)
            return ui.input_buffer.text, names, [completion.text for completion in ui.menu.items] if ui.menu else []

        _ui, (text, after_backspace, after_o) = drive_ui(runtime, drive)
        self.assertEqual(text, "/lo")
        self.assertIn("/loop", after_backspace)
        self.assertIn("/loop", after_o)
        self.assertNotIn("/lang", after_o)

class Folding(unittest.TestCase):
    def test_tool_work_is_one_gray_line_that_unfolds_on_click_and_with_ctrl_o(self):
        runtime, _ = make_runtime({"a.txt": "alpha\nbeta\n"}, turns=[
            '```memuai-tool\n[{"tool":"Read","args":{"file_path":"a.txt"}},{"tool":"Grep","args":{"pattern":"beta"}}]\n```',
            "It has two lines."])

        async def drive(ui, keys):
            keys.send_text("what is in a.txt?\r")
            for _ in range(80):
                await asyncio.sleep(0.05)
                if not ui.busy and len(runtime.session.messages) >= 5:
                    break
            folded = texts(ui.transcript.viewport(40, 110))
            row = next(index for index, line in enumerate(folded) if "▸" in line)
            ui.transcript._click(row)
            unfolded = texts(ui.transcript.viewport(40, 110))
            ui.transcript._click(next(index for index, line in enumerate(unfolded) if "▾" in line))
            keys.send_text("\x0f")
            await settle()
            everything = texts(ui.transcript.viewport(40, 110))
            return folded, unfolded, everything

        _ui, (folded, unfolded, everything) = drive_ui(runtime, drive)
        summary = next(line for line in folded if "▸" in line)
        self.assertIn("Read a.txt", summary)
        self.assertIn("searched for", summary)
        self.assertFalse(any("a.txt  Read" in line for line in folded))
        self.assertTrue(any(line.strip().startswith("Read") and "a.txt" in line for line in unfolded))
        self.assertTrue(any(line.strip().startswith("Search") for line in unfolded))
        self.assertTrue(any(line.strip().startswith("Read") for line in everything))

class StatusLine(unittest.TestCase):
    def test_the_status_line_number_can_be_switched_to_tokens_and_more(self):
        runtime, backend = make_runtime(turns=["hi there"])

        async def drive(ui, keys):
            keys.send_text("hello\r")

            await until(lambda: not ui.busy and len(backend.requests) >= 2 and bool(runtime.session.meta.get("title")), 10)
            seen = {}
            for key, _label in ui.METRICS:
                seen[key] = ui.metric_text(key)
            task = asyncio.ensure_future(ui.choose_metric())
            await settle(0.3)
            keys.send_text("\x1b[B\r")
            await task
            return seen, ui.settings.get("footer_metric"), ui.metric_text()

        _ui, (seen, chosen, shown) = drive_ui(runtime, drive)
        self.assertRegex(seen["tokens"], r"\d+(\.\d+)?k? in / \d+(\.\d+)?k? out|\d+ in / \d+ out")
        self.assertRegex(seen["lines"], r"^\+\d+ −\d+$")
        self.assertRegex(seen["duration"], r"min")
        self.assertEqual(seen["off"], "")
        self.assertEqual(chosen, "tokens")
        self.assertEqual(shown, seen["tokens"])

class ClipboardKeys(unittest.TestCase):
    def test_ctrl_v_attaches_a_screenshot_and_alt_v_too(self):
        runtime, _ = make_runtime()
        image = {"media_type": "image/png", "data": "AAAA", "width": 1, "height": 1, "name": "clipboard.png"}

        async def drive(ui, keys):
            with patch("app.cli.tui.app.clipboard_image", return_value=image), \
                    patch("app.cli.clipboard.has_image", return_value=True), \
                    patch("app.cli.clipboard.has_files", return_value=False), \
                    patch("app.cli.clipboard.sequence_number", return_value=7):
                keys.send_text("\x16")
                await settle(0.4)
                keys.send_text("\x1bv")
                await settle(0.4)
            return ui.input_buffer.text

        _ui, text = drive_ui(runtime, drive)
        self.assertIn("[Image #1]", text)
        self.assertIn("[Image #2]", text)

    def test_ctrl_v_pastes_clipboard_text_when_there_is_no_image(self):
        runtime, _ = make_runtime()

        async def drive(ui, keys):
            with patch("app.cli.tui.app.clipboard_image", return_value=None), \
                    patch("app.cli.clipboard.has_image", return_value=False), \
                    patch("app.cli.clipboard.has_files", return_value=False), \
                    patch("app.cli.clipboard.sequence_number", return_value=3), \
                    patch("app.cli.clipboard.read_text", return_value="text from the clipboard"):
                keys.send_text("\x16")
                await settle(0.4)
            return ui.input_buffer.text

        _ui, text = drive_ui(runtime, drive)
        self.assertEqual(text, "text from the clipboard")

    def test_an_image_appearing_on_the_clipboard_shows_a_hint_in_the_footer(self):
        runtime, _ = make_runtime()

        async def drive(ui, keys):
            with patch("app.cli.clipboard.sequence_number", side_effect=[1] + [2] * 50), \
                    patch("app.cli.clipboard.has_image", return_value=True):
                await asyncio.sleep(0.9)
            return ui.clipboard_image_seen, "".join(text for _style, text, *rest in ui._footer_left())

        _ui, (seen, footer) = drive_ui(runtime, drive)
        self.assertTrue(seen)
        self.assertIn("Alt+V", footer)

class Connecting(unittest.TestCase):
    def test_typing_works_while_models_load_and_the_message_waits_for_them(self):
        runtime, backend = make_runtime(turns=["Hello."])

        async def drive(ui, keys):
            runtime._ready = asyncio.Event()
            ui.connecting = True
            keys.send_text("hello there\r")
            await settle(0.5)
            before = len(backend.requests)
            busy_while_waiting = ui.busy
            ui.connecting = False
            runtime._ready.set()
            for _ in range(60):
                await asyncio.sleep(0.05)
                if backend.requests and not ui.busy:
                    break
            return before, busy_while_waiting, len(backend.requests)

        _ui, (before, waiting, after) = drive_ui(runtime, drive)
        self.assertEqual(before, 0)
        self.assertTrue(waiting)
        self.assertGreaterEqual(after, 1)

class DialogGuard(unittest.TestCase):
    def test_keys_typed_a_moment_before_an_agent_dialog_cannot_answer_it(self):
        from app.cli.tui import dialogs as dlg
        runtime, _ = make_runtime()

        async def drive(ui, keys):
            dialog = dlg.Dialog(title="Allow?", options=[dlg.Option("Yes", "yes"), dlg.Option("No", "no")], guard=True)
            task = asyncio.ensure_future(ui.open_dialog(dialog))
            await asyncio.sleep(0.1)
            keys.send_text("1")
            await asyncio.sleep(0.2)
            early = task.done()
            await asyncio.sleep(0.7)
            keys.send_text("1")
            answer = await asyncio.wait_for(task, 3)
            return early, answer

        _ui, (early, answer) = drive_ui(runtime, drive)
        self.assertFalse(early)
        self.assertEqual(answer, "yes")

def press_escape(ui):
    """What the Windows console reader delivers for Esc: one ready key press (no VT parser in between)."""
    from prompt_toolkit.key_binding.key_processor import KeyPress
    from prompt_toolkit.keys import Keys
    ui.app.key_processor.feed(KeyPress(Keys.Escape, "\x1b"))
    ui.app.key_processor.process_keys()

async def until(condition, timeout=3.0):
    started = time.perf_counter()
    while time.perf_counter() - started < timeout:
        if condition():
            return time.perf_counter() - started
        await asyncio.sleep(0.002)
    return None

class Responsiveness(unittest.TestCase):
    """Round 13: nothing in the interface waits before it reacts."""

    def test_escape_closes_a_dialog_at_once_and_alt_chords_still_work(self):
        runtime, _ = make_runtime()

        async def drive(ui, keys):
            keys.send_text("/settings")
            await settle(0.2)
            keys.send_text("\r")
            await until(lambda: bool(ui.dialogs))
            await settle(0.2)
            focused_a_text_field = ui.app.layout.current_buffer is ui.dialog_filter
            press_escape(ui)
            closing = await until(lambda: not ui.dialogs)

            before = bool(ui.settings.get("thinking"))
            keys.send_text("\x1bt")
            await settle(0.3)
            return focused_a_text_field, closing, before, bool(ui.settings.get("thinking")), ui.input_buffer.text

        _ui, (text_field, closing, before, after, typed) = drive_ui(runtime, drive)
        self.assertTrue(text_field)
        self.assertIsNotNone(closing)
        self.assertLess(closing, 0.08)
        self.assertNotEqual(before, after)
        self.assertEqual(typed, "")

    def test_usage_opens_before_the_account_limits_arrive(self):
        runtime, _ = make_runtime()

        release = asyncio.Event()

        async def slow_limits(ctx):
            from rich.text import Text
            await asyncio.wait_for(release.wait(), 5)
            ctx.print(Text("LIMITS ARRIVED"))

        async def drive(ui, keys):
            with patch("app.cli.commands.info_cmds._limits", slow_limits):
                keys.send_text("/usage")
                await settle(0.2)
                keys.send_text("\r")
                opened = await until(lambda: bool(ui.dialogs))
                early = ui.dialogs[0].body if ui.dialogs else ""
                release.set()
                await until(lambda: bool(ui.dialogs) and "LIMITS ARRIVED" in ui.dialogs[0].body, 3)
                late = ui.dialogs[0].body if ui.dialogs else ""
            return opened, early, late

        _ui, (opened, early, late) = drive_ui(runtime, drive)
        self.assertIsNotNone(opened, "the window waited for the account limits")
        self.assertLess(opened, 1.5)
        self.assertIn("Usage", early)
        self.assertIn("Loading activity and account limits", early)
        self.assertNotIn("LIMITS ARRIVED", early)
        self.assertIn("LIMITS ARRIVED", late)
        self.assertNotIn("Loading activity", late)

    def test_the_model_list_opens_from_what_is_known_and_is_refreshed_in_place(self):
        from app.cli.llm import ModelInfo
        runtime, _ = make_runtime()

        async def slow_models(*, refresh=False):
            await asyncio.sleep(0.5)
            runtime.router._models = [ModelInfo(id="test-model", provider="custom", context=200_000),
                                      ModelInfo(id="brand-new-model", provider="custom", context=100_000)]
            return runtime.router._models

        async def drive(ui, keys):
            runtime.router.models = slow_models
            keys.send_text("/model")
            await settle(0.2)
            keys.send_text("\r")
            opened = await until(lambda: bool(ui.dialogs))

            first = [option.value for option in ui.dialogs[0].options] if ui.dialogs else []
            await until(lambda: bool(ui.dialogs) and len(ui.dialogs[0].options) > 1, 3)
            later = [option.value for option in ui.dialogs[0].options] if ui.dialogs else []
            press_escape(ui)
            await until(lambda: not ui.dialogs)
            return opened, first, later

        _ui, (opened, first, later) = drive_ui(runtime, drive)
        self.assertLess(opened, 0.3)
        self.assertEqual(first, ["test-model"])
        self.assertIn("brand-new-model", later)

class Suggestions(unittest.TestCase):
    def test_arrows_move_through_the_list_without_rewriting_what_was_typed(self):
        runtime, _ = make_runtime()

        async def drive(ui, keys):
            keys.send_text("/co")
            await settle(0.2)
            listed = [item.text for item in ui.menu.items]
            nothing_picked = ui.menu.index
            keys.send_text("\x1b[B\x1b[B")
            await settle(0.2)
            typed, index = ui.input_buffer.text, ui.menu.index
            picked = ui.menu.items[index].text
            press_escape(ui)
            await settle(0.1)
            hidden = ui.menu is None
            keys.send_text("\t")
            await settle(0.1)
            return listed, nothing_picked, typed, index, picked, hidden, bool(ui.menu and ui.menu.items)

        _ui, (listed, nothing_picked, typed, index, picked, hidden, back) = drive_ui(runtime, drive)
        self.assertGreater(len(listed), 2)
        self.assertIsNone(nothing_picked)
        self.assertEqual(typed, "/co")
        self.assertEqual(index, 1)
        self.assertEqual(picked, listed[1])
        self.assertTrue(hidden)
        self.assertTrue(back)

    def test_a_fully_typed_command_runs_even_when_it_is_not_the_first_suggestion(self):
        runtime, _ = make_runtime()

        async def drive(ui, keys):
            keys.send_text("/version")
            await settle(0.2)
            suggested = [item.text for item in ui.menu.items] if ui.menu else []
            keys.send_text("\r")
            await settle(0.4)
            return suggested, "\n".join(texts(ui.transcript.viewport(40, 110))), ui.input_buffer.text

        _ui, (suggested, shown, left) = drive_ui(runtime, drive)
        self.assertNotIn("/version", suggested)
        self.assertIn("MemuCode ", shown)
        self.assertEqual(left, "")

    def test_file_names_are_matched_off_the_interface_thread_and_only_the_newest_is_shown(self):
        files = {f"src/pkg{index // 40}/module_{index}.py": "x = 1\n" for index in range(400)}
        runtime, _ = make_runtime(files)

        async def drive(ui, keys):
            keys.send_text("look at @module_39")
            await until(lambda: bool(ui.menu and ui.menu.text.endswith("@module_39") and ui.menu.items), 3)
            names = [item.text for item in ui.menu.items]
            keys.send_text("\r")
            await settle(0.2)
            return names, ui.input_buffer.text

        _ui, (names, text) = drive_ui(runtime, drive)
        self.assertIn("@src/pkg0/module_39.py", names[:1])
        self.assertTrue(all("module_39" in name for name in names[:11]))
        self.assertEqual(text, "look at @src/pkg0/module_39.py ")

    def test_a_folder_is_completed_without_a_trailing_separator_and_enter_sends_the_path(self):
        import os
        from prompt_toolkit.document import Document
        from app.cli.tui.app import _continues_line
        from app.cli.tui.input import MemuCompleter
        runtime, _ = make_runtime({"docs/guide/readme.md": "hi\n", "docs/api.md": "x\n"})
        completer = MemuCompleter(lambda: [], lambda: [], lambda: [], lambda: runtime.root)
        typed = "/cd " + str(runtime.root / "do")
        found = list(completer.get_completions(Document(typed, len(typed))))
        self.assertEqual([item.text for item in found], [str(runtime.root / "docs")])
        self.assertTrue(found[0].display_text.endswith(os.sep))
        self.assertEqual(completer.kind(typed), "path")
        self.assertEqual(completer.span(typed), len(str(runtime.root / "do")))

        self.assertTrue(_continues_line("first line\\"))
        self.assertTrue(_continues_line("explain this \\"))
        self.assertFalse(_continues_line("/cd C:\\Users\\me\\"))
        self.assertFalse(_continues_line("/cd " + str(runtime.root) + "\\"))
        self.assertFalse(_continues_line("open C:\\Users\\me\\"))
        self.assertFalse(_continues_line("look in src\\app\\"))
        self.assertFalse(_continues_line("C:\\"))
        self.assertFalse(_continues_line("two backslashes\\\\"))
        self.assertFalse(_continues_line("no backslash"))

        root = runtime.root

        async def drive(ui, keys):
            keys.send_text("/cd " + str(root / "docs") + os.sep)
            await until(lambda: bool(ui.menu and ui.menu.kind == "path" and ui.menu.items), 3)
            listed = [item.text for item in ui.menu.items]
            keys.send_text("\r")
            await settle(0.5)
            return listed, ui.input_buffer.text, str(runtime.session.cwd or runtime.root)

        _ui, (listed, left, cwd) = drive_ui(runtime, drive)
        self.assertEqual(listed, [str(root / "docs" / "guide")])
        self.assertEqual(left, "")
        self.assertEqual(os.path.normcase(cwd.rstrip("\\/")), os.path.normcase(str(root / "docs")))

class InputFixes(unittest.TestCase):
    def test_a_pasted_path_that_cannot_be_a_chip_is_put_in_as_a_path_without_an_error(self):
        runtime, _ = make_runtime({"notes.txt": "small file\n", "big.bin": "x"})
        (runtime.root / "big.txt").write_text("y" * 500_000, encoding="utf-8")
        (runtime.root / "folder dir").mkdir()

        async def drive(ui, keys):
            seen = {}
            for name in ("notes.txt", "big.txt", "folder dir"):
                ui.input_buffer.reset()
                ui.handle_paste(str(runtime.root / name))
                seen[name] = ui.input_buffer.text
            return seen, "\n".join(texts(ui.transcript.viewport(40, 110)))

        _ui, (seen, shown) = drive_ui(runtime, drive)
        self.assertRegex(seen["notes.txt"], r"^\[File #\d+: notes\.txt\] $")
        self.assertEqual(seen["big.txt"], str(runtime.root / "big.txt") + " ")
        self.assertEqual(seen["folder dir"], '"' + str(runtime.root / "folder dir") + '" ')
        self.assertNotIn("Could not attach", shown)
        self.assertNotIn("400 KB", shown)

    def test_a_typed_path_to_a_big_file_is_sent_as_a_message(self):
        runtime, backend = make_runtime(turns=["ok"])
        (runtime.root / "big.txt").write_text("y" * 500_000, encoding="utf-8")

        async def drive(ui, keys):
            keys.send_text(str(runtime.root / "big.txt") + "\r")
            await until(lambda: bool(backend.requests) and not ui.busy, 4)
            return ui.input_buffer.text

        _ui, left = drive_ui(runtime, drive)
        self.assertEqual(left, "")
        self.assertIn("big.txt", json.dumps(backend.requests[0].messages))

    def test_ctrl_a_selects_the_whole_message_and_typing_replaces_it(self):
        runtime, _ = make_runtime()

        async def drive(ui, keys):
            keys.send_text("first line")
            keys.send_text("\x1b\r")
            keys.send_text("second line")
            await settle(0.2)
            keys.send_text("\x01")
            await settle(0.1)
            start, end = ui.input_buffer.document.selection_range()
            selected = ui.input_buffer.text[start:end]
            with patch("app.cli.tui.app.copy_text", return_value=True) as copied:
                keys.send_text("\x03")
                await settle(0.1)
            kept = ui.input_buffer.text
            keys.send_text("x")
            await settle(0.1)
            return selected, copied.call_args[0][0] if copied.called else None, kept, ui.input_buffer.text

        _ui, (selected, copied, kept, after) = drive_ui(runtime, drive)
        self.assertEqual(selected, "first line\nsecond line")
        self.assertEqual(copied, "first line\nsecond line")
        self.assertEqual(kept, "first line\nsecond line")
        self.assertEqual(after, "x")

    def test_finishing_work_is_silent_unless_the_bell_was_asked_for(self):
        runtime, _ = make_runtime()

        async def drive(ui, keys):
            rings = []
            ui._ring = lambda: rings.append(1)
            with patch("app.desktop_notifications.show_completion") as toast:
                ui._notify("Done", "text")
                await settle(0.1)
                default = (len(rings), toast.call_count)
                ui.settings.overrides["notifications"] = "bell"
                ui.settings.reload()
                ui._notify("Done", "text")
                await settle(0.1)
                asked = (len(rings), toast.call_count)
                ui.settings.overrides["notifications"] = "off"
                ui.settings.reload()
                ui._notify("Done", "text")
                await settle(0.1)
            return default, asked, (len(rings), toast.call_count)

        _ui, (default, asked, off) = drive_ui(runtime, drive)
        self.assertEqual(default, (0, 1))
        self.assertEqual(asked, (1, 1))
        self.assertEqual(off, (1, 1))

    def test_effort_says_what_it_does_for_the_current_model(self):
        from app.cli.llm import ModelInfo
        runtime, _ = make_runtime()
        router = runtime.router
        del router.resolve
        router._models = [
            ModelInfo(id="antigravity-claude-opus-4-6-thinking", provider="antigravity", raw="claude-opus-4-6-thinking"),
            ModelInfo(id="antigravity-gemini-3.8-flash", provider="antigravity", raw="gemini-3.8-flash"),
            ModelInfo(id="antigravity-gemini-3.8-flash-low", provider="antigravity", raw="gemini-3.8-flash-low"),
            ModelInfo(id="antigravity-gemini-3.6-flash-high", provider="antigravity", raw="gemini-3.6-flash-high"),
            ModelInfo(id="kiro-claude-sonnet-4-6", provider="kiro", raw="claude-sonnet-4-6"),
        ]
        self.assertTrue(router.effort_support("antigravity-claude-opus-4-6-thinking")[0])
        self.assertIn("budget", router.effort_support("antigravity-claude-opus-4-6-thinking")[1])
        self.assertTrue(router.effort_support("antigravity-gemini-3.8-flash")[0])
        self.assertFalse(router.effort_support("antigravity-gemini-3.8-flash-low")[0])
        self.assertIn("fixed by the model name", router.effort_support("antigravity-gemini-3.8-flash-low")[1])
        self.assertFalse(router.effort_support("antigravity-gemini-3.6-flash-high")[0])
        supported, why = router.effort_support("kiro-claude-sonnet-4-6")
        self.assertFalse(supported)
        self.assertIn("kiro", why)
        self.assertIn("does not affect", why)

        async def drive(ui, keys):
            runtime.model = "kiro-claude-sonnet-4-6"
            keys.send_text("/effort")
            await settle(0.2)
            keys.send_text("\r")
            await until(lambda: bool(ui.dialogs))
            body = ui.dialogs[0].body
            keys.send_text("\x1b[B\r")
            await settle(0.4)
            return body, "\n".join(texts(ui.transcript.viewport(40, 110)))

        _ui, (body, shown) = drive_ui(runtime, drive)
        self.assertIn("does not support a reasoning level", body)
        self.assertIn("saved, but it has no effect on the current model", " ".join(shown.split()))

if __name__ == "__main__":
    unittest.main()
