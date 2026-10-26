"""The block transcript: lazy rendering, folding in place, selection, and the gray activity summaries."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from prompt_toolkit.data_structures import Point
from prompt_toolkit.keys import Keys
from prompt_toolkit.key_binding.key_processor import KeyPress
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType

from app.cli.tools.base import ToolCall, ToolResult
from app.cli.tui.activity import Step, summary_parts
from app.cli.tui.input import is_paste_burst, keys_to_paste
from app.cli.tui.transcript import Transcript, TranscriptControl, ansi_to_rows, row_text

def texts(rows):
    return [row_text(row) for row in rows]

def mouse(kind, column, row=0, button=MouseButton.LEFT):
    return MouseEvent(Point(column, row), kind, button, frozenset())

class TranscriptBlocks(unittest.TestCase):
    def test_resize_reflows_and_uses_current_viewport(self):
        transcript = Transcript(lambda count: None)
        transcript.append("abcdefghijkl\n")
        control = TranscriptControl(transcript, lambda: None)
        content = control.create_content(4, 2)
        self.assertEqual(content.line_count, 2)
        self.assertEqual(texts(transcript._view), ["efgh", "ijkl"])
        transcript.scroll(1)
        control.create_content(4, 2)
        self.assertEqual(texts(transcript._view), ["abcd", "efgh"])
        control.create_content(12, 3)
        self.assertEqual(texts(transcript._view), ["abcdefghijkl"])

    def test_scroll_and_copy_selection(self):
        copied = []
        transcript = Transcript(copied.append)
        transcript.append("first\nsecond\nthird\n")
        transcript.viewport(2, 80)
        self.assertEqual(texts(transcript._view), ["second", "third"])
        transcript.scroll(1)
        transcript.viewport(2, 80)
        self.assertEqual(texts(transcript._view), ["first", "second"])
        transcript.anchor = (0, 1)
        transcript.caret = (1, 3)
        self.assertEqual(transcript.selected_text(), "irst\nsec")

    def test_release_copies_selection_and_reports_count(self):
        copied = []
        transcript = Transcript(copied.append)
        transcript.append("abcde\n")
        transcript.viewport(2, 80)
        with patch("app.cli.tui.transcript.copy_text", return_value=True) as clipboard:
            transcript.mouse(mouse(MouseEventType.MOUSE_DOWN, 1))
            transcript.mouse(mouse(MouseEventType.MOUSE_MOVE, 3))
            transcript.mouse(mouse(MouseEventType.MOUSE_UP, 4))
        clipboard.assert_called_once_with("bcd")
        self.assertEqual(copied, [3])

    def test_plain_mouse_motion_never_asks_for_a_redraw(self):
        transcript = Transcript(lambda count: None)
        transcript.append("hello\n")
        transcript.viewport(3, 40)
        self.assertFalse(transcript.mouse(mouse(MouseEventType.MOUSE_MOVE, 2, button=MouseButton.NONE)))

    def test_double_click_selects_a_word_and_triple_click_the_line(self):
        transcript = Transcript(lambda count: None)
        transcript.append("call foo_bar now\n")
        transcript.viewport(3, 40)

        def click(column):
            transcript.mouse(mouse(MouseEventType.MOUSE_DOWN, column))
            transcript.mouse(mouse(MouseEventType.MOUSE_UP, column))

        with patch("app.cli.tui.transcript.copy_text", return_value=True) as clipboard:
            click(7)
            click(7)
            clipboard.assert_called_with("foo_bar")
            click(7)
            clipboard.assert_called_with("call foo_bar now")

    def test_expandable_block_unfolds_in_place(self):
        transcript = Transcript(lambda count: None)
        transcript.add("intro\n")
        block = transcript.add(
            lambda width, expanded: "summary\n" + ("detail 1\ndetail 2\ndetail 3\n" if expanded else ""), expandable=True)
        transcript.add("after\n")
        self.assertEqual(texts(transcript.viewport(6, 40)), ["intro", "summary", "after"])
        transcript._click(1)
        self.assertEqual(texts(transcript.viewport(6, 40)),
                         ["intro", "summary", "detail 1", "detail 2", "detail 3", "after"])
        self.assertTrue(block.is_expanded(False))
        transcript._click(1)
        self.assertEqual(texts(transcript.viewport(6, 40)), ["intro", "summary", "after"])
        self.assertEqual(transcript.offset, 0)

    def test_clicked_header_stays_on_its_screen_row(self):
        transcript = Transcript(lambda count: None)
        for index in range(8):
            transcript.add(f"line {index}\n")
        transcript.add(lambda width, expanded: "summary\n" + ("d1\nd2\nd3\n" if expanded else ""), expandable=True)
        transcript.add("tail\n")
        header_row = texts(transcript.viewport(4, 40)).index("summary")
        transcript._click(header_row)
        self.assertEqual(texts(transcript.viewport(4, 40)).index("summary"), header_row)

    def test_theme_change_repaints_lazy_blocks(self):
        transcript = Transcript(lambda count: None)
        colour = {"value": "red"}
        transcript.add(lambda width, expanded: f"\x1b[31m{colour['value']}\x1b[0m\n")
        transcript.viewport(2, 20)
        colour["value"] = "blue"
        self.assertEqual(texts(transcript.viewport(2, 20))[-1], "red")
        transcript.restyle()
        self.assertEqual(texts(transcript.viewport(2, 20))[-1], "blue")

    def test_wide_characters_wrap_by_cells(self):
        self.assertEqual(texts(ansi_to_rows("漢字漢字漢", 5)), ["漢字", "漢字", "漢"])

    def test_history_position_holds_while_new_blocks_arrive(self):
        transcript = Transcript(lambda count: None)
        for index in range(10):
            transcript.add(f"line {index}\n")
        transcript.viewport(3, 40)
        transcript.scroll(2)
        before = texts(transcript.viewport(3, 40))
        transcript.add("new\n")
        self.assertEqual(texts(transcript.viewport(3, 40)), before)
        transcript.scroll_to_end()
        self.assertEqual(texts(transcript.viewport(3, 40))[-1], "new")

def step(name, args=None, ok=True, summary="", diff="", display="", status="done", detail="", meta=None):
    call = ToolCall(name=name, args=args or {}, display=display)
    call.status = status
    return Step(call, ToolResult(ok=ok, summary=summary, diff=diff, detail=detail, meta=meta or {},
                                 error="" if ok else summary))

class ActivitySummary(unittest.TestCase):
    def line(self, *steps, width=100):
        return "".join(text for text, _style in summary_parts(list(steps), width))

    def test_one_read_names_the_file_and_many_count_them(self):
        one = step("Read", {"file_path": "src/app.py"}, display="src/app.py")
        self.assertEqual(self.line(one), "Read src/app.py")
        many = [step("Read", {"file_path": f"f{i}.py"}) for i in range(3)]
        self.assertEqual(self.line(*many), "Read 3 files")

    def test_a_turn_of_work_reads_as_one_sentence(self):
        text = self.line(
            step("Read", {"file_path": "app.py"}), step("Grep", {"pattern": "def add"}),
            step("Edit", {"file_path": "app.py"}, diff="@@ -1 +1 @@\n-a\n+b\n"),
            step("Bash", {"command": "pytest -q"}, display="pytest -q"))
        self.assertEqual(text, "Read app.py, searched for “def add”, edited app.py (+1 −1), ran pytest -q")

    def test_failures_and_denials_stay_visible(self):
        text = self.line(step("Bash", {"command": "make"}, display="make", ok=False, summary="exit 2"),
                         step("Edit", {"file_path": "x.py"}, ok=False, status="denied", summary="Denied"))
        self.assertIn("1 failed", text)
        self.assertIn("denied", text)

    def test_long_summaries_are_shortened_to_the_width(self):
        steps = [step("Read", {"file_path": f"a/b/c{i}.py"}) for i in range(1)] + [
            step("Bash", {"command": "x " * 80}, display="x " * 80)]
        self.assertLessEqual(len(self.line(*steps, width=40)), 40)

    def test_todo_and_question_do_not_clutter_a_summary(self):
        text = self.line(step("Read", {"file_path": "a.py"}), step("TodoWrite"), step("AskUserQuestion"))
        self.assertEqual(text, "Read a.py")

class PasteDetection(unittest.TestCase):
    @staticmethod
    def keys(text):
        out = []
        for char in text:
            if char == "\r":
                out.append(KeyPress(Keys.ControlM, "\r"))
            else:
                out.append(KeyPress(char, char))
        return out

    def test_typing_with_a_trailing_enter_is_not_a_paste(self):
        self.assertFalse(is_paste_burst(self.keys("hello\r")))
        self.assertFalse(is_paste_burst(self.keys("hi")))

    def test_text_after_a_newline_or_a_long_run_is_a_paste(self):
        self.assertTrue(is_paste_burst(self.keys("a\rb")))
        self.assertTrue(is_paste_burst(self.keys("x" * 40)))

    def test_a_burst_becomes_one_paste_event(self):
        events = keys_to_paste(self.keys("line1\rline2\r") + [KeyPress(Keys.Escape, ""), KeyPress(Keys.ControlJ, "\n")])
        self.assertEqual([event.key for event in events], [Keys.BracketedPaste])
        self.assertEqual(events[0].data, "line1\nline2\n\n")

    def test_other_keys_stay_in_order(self):
        events = keys_to_paste(self.keys("abc") + [KeyPress(Keys.Up, "")] + self.keys("def"))
        self.assertEqual([event.key for event in events], [Keys.BracketedPaste, Keys.Up, Keys.BracketedPaste])

if __name__ == "__main__":
    unittest.main()
