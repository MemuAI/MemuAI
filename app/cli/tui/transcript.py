"""The scrollable transcript of the terminal interface.

Everything MemuCode shows above the input box is a list of **blocks**. A block
keeps its *source* (an ANSI string or a function of the width) and renders it
lazily into wrapped rows, cached per width, so

* resizing the window reflows markdown, tables and boxes instead of chopping
  already-rendered text;
* changing the theme repaints the whole history;
* an expandable block (a tool summary, a thought, a long message) unfolds in
  place when it is clicked — the clicked line stays where it is on the screen;
* only the rows that are visible are ever assembled, so a session with
  thousands of blocks costs the same per keystroke as an empty one.

The mouse wheel and PageUp/PageDown scroll it independently of the input box;
selecting text with the mouse copies it on release, a double click selects a
word and a triple click a line.
"""
from __future__ import annotations

import time
from typing import Any, Callable, List, Optional, Tuple, Union

from prompt_toolkit.formatted_text import ANSI, to_formatted_text
from prompt_toolkit.layout.controls import UIContent, UIControl
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType
from prompt_toolkit.utils import get_cwidth

from ..clipboard import copy_text

Row = List[Tuple[str, str]]
Source = Union[str, Callable[[int, bool], str]]

SELECTION_STYLE = " class:transcript.selection"

def cell_width(char: str) -> int:
    if char < "̀":
        return 1 if char >= " " else 0
    return get_cwidth(char)

def text_width(text: str) -> int:
    if text.isascii():
        return len(text)
    return sum(cell_width(char) for char in text)

def wrap_row(runs: Row, width: int) -> List[Row]:
    """One logical line → rows no wider than ``width`` cells (hard wrap)."""
    rows: List[Row] = []
    current: Row = []
    used = 0
    for style, text in runs:
        if not text:
            continue
        if text.isascii() and used + len(text) <= width:
            current.append((style, text))
            used += len(text)
            continue
        pending: List[str] = []
        for char in text:
            step = cell_width(char)
            if step and used + step > width and (current or pending):
                if pending:
                    current.append((style, "".join(pending)))
                    pending = []
                rows.append(current)
                current, used = [], 0
            pending.append(char)
            used += step
        if pending:
            current.append((style, "".join(pending)))
    rows.append(current)
    return rows

def ansi_to_rows(text: str, width: int) -> List[Row]:
    """ANSI text (possibly many lines) → wrapped rows of (style, text) runs."""
    width = max(1, width)
    if not text:
        return []
    lines: List[Row] = [[]]
    for fragment in to_formatted_text(ANSI(text.replace("\r\n", "\n").replace("\r", "\n"))):
        style, chunk = fragment[0], fragment[1]
        if not chunk:
            continue
        pieces = chunk.expandtabs(4).split("\n")
        for index, piece in enumerate(pieces):
            if index:
                lines.append([])
            if piece:
                line = lines[-1]
                if line and line[-1][0] == style:
                    line[-1] = (style, line[-1][1] + piece)
                else:
                    line.append((style, piece))
    if len(lines) > 1 and not lines[-1]:
        lines.pop()
    rows: List[Row] = []
    for line in lines:
        if sum(len(run[1]) for run in line) <= width and all(run[1].isascii() for run in line):
            rows.append(line)
        else:
            rows.extend(wrap_row(line, width))
    return rows

def row_text(row: Row) -> str:
    return "".join(text for _style, text in row)

def _cells_to_index(text: str, cell: int) -> int:
    """Character index of the character that covers screen cell ``cell``."""
    if text.isascii():
        return min(cell, len(text))
    used = 0
    for index, char in enumerate(text):
        used += cell_width(char)
        if used > cell:
            return index
    return len(text)

def _slice_cells(row: Row, first: int, last: int) -> Row:
    """The runs of ``row`` between cells [first, last) with the selection style added."""
    out: Row = []
    position = 0
    for style, text in row:
        start = position
        width = text_width(text)
        position += width
        if position <= first or start >= last:
            out.append((style, text))
            continue
        low = _cells_to_index(text, max(0, first - start))
        high = _cells_to_index(text, min(width, last - start))
        if low > 0:
            out.append((style, text[:low]))
        if high > low:
            out.append((style + SELECTION_STYLE, text[low:high]))
        if high < len(text):
            out.append((style, text[high:]))
    if last > position:
        out.append(("class:transcript.selection", " " * min(last - position, 1)))
    return out

class Block:
    """One entry of the transcript."""

    def __init__(self, source: Source, *, expandable: bool = False, expanded: Optional[bool] = None,
                 on_click: Optional[Callable[[], None]] = None, kind: str = "") -> None:
        self.source = source
        self.expandable = expandable
        self.expanded = expanded
        self.on_click = on_click
        self.kind = kind
        self.version = 0
        self._key: Optional[tuple] = None
        self._rows: List[Row] = []

    def is_expanded(self, default: bool) -> bool:
        if not self.expandable:
            return False
        return default if self.expanded is None else self.expanded

    def rows(self, width: int, default_expanded: bool, theme: int) -> List[Row]:
        expanded = self.is_expanded(default_expanded)
        lazy = callable(self.source)
        key = (width, expanded, self.version, theme if lazy else 0)
        if key != self._key:
            text = self.source(width, expanded) if lazy else self.source          # type: ignore[operator]
            self._rows = ansi_to_rows(text, width)
            self._key = key
        return self._rows

    def update(self) -> None:
        """The content changed: render it again next time."""
        self.version += 1

class Transcript:
    def __init__(self, copied: Callable[[int], None]) -> None:
        self.blocks: List[Block] = []
        self.offset = 0
        self.height = 20
        self.width = 80
        self.copied = copied
        self.expand_all = False
        self.theme = 0
        self.changed: Callable[[], None] = lambda: None
        self.anchor: Optional[Tuple[int, int]] = None
        self.caret: Optional[Tuple[int, int]] = None
        self._visible: List[Tuple[Block, int]] = []
        self._view: List[Row] = []
        self._press: Optional[Tuple[int, int]] = None
        self._clicks = 0
        self._click_at = 0.0
        self._click_cell: Tuple[int, int] = (-1, -1)

    def add(self, block: Union[Block, str, Callable[[int, bool], str]], **kwargs: Any) -> Block:
        if not isinstance(block, Block):
            block = Block(block, **kwargs)
        self.blocks.append(block)
        if self.offset:
            self.offset += len(block.rows(self.width, self.expand_all, self.theme))
        self.changed()
        return block

    def append(self, chunk: str) -> Block:
        """Compatibility: a chunk of finished ANSI lines."""
        return self.add(chunk)

    def clear(self) -> None:
        self.blocks = []
        self.offset = 0
        self.anchor = self.caret = None
        self._visible = []
        self._view = []
        self.changed()

    def restyle(self) -> None:
        """The theme changed: every lazy block renders itself again."""
        self.theme += 1
        self.changed()

    def set_expand_all(self, value: bool) -> None:
        self.expand_all = value
        for block in self.blocks:
            block.expanded = None
        self.changed()

    def scroll(self, delta: int) -> None:
        self.offset = max(0, self.offset + delta)
        self.anchor = self.caret = None
        self.changed()

    def scroll_to_end(self) -> None:
        if self.offset:
            self.offset = 0
            self.anchor = self.caret = None
            self.changed()

    def viewport(self, height: int, width: int) -> List[Row]:
        """The rows that fit the window, ending ``offset`` rows above the last one."""
        self.height = max(1, height)
        self.width = max(1, width)
        need = self.offset + self.height
        collected: List[Tuple[Block, List[Row]]] = []
        count = 0
        for block in reversed(self.blocks):
            rows = block.rows(self.width, self.expand_all, self.theme)
            collected.append((block, rows))
            count += len(rows)
            if count >= need:
                break
        collected.reverse()
        flat: List[Row] = []
        owners: List[Tuple[Block, int]] = []
        for block, rows in collected:
            for index, row in enumerate(rows):
                flat.append(row)
                owners.append((block, index))
        if count < need:
            self.offset = max(0, len(flat) - self.height)
        end = len(flat) - self.offset
        start = max(0, end - self.height)
        self._view = flat[start:end]
        self._visible = owners[start:end]
        return self._view

    def selection(self) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
        if self.anchor is None or self.caret is None or self.anchor == self.caret:
            return None
        first, last = sorted((self.anchor, self.caret))
        return first, last

    def style_row(self, index: int, row: Row) -> Row:
        span = self.selection()
        if span is None:
            return row
        (first_row, first_col), (last_row, last_col) = span
        if index < first_row or index > last_row:
            return row
        width = text_width(row_text(row))
        low = first_col if index == first_row else 0
        high = last_col if index == last_row else max(width, 1)
        return _slice_cells(row, low, max(high, low + 1)) if high > low else row

    def selected_text(self) -> str:
        span = self.selection()
        if span is None or not self._view:
            return ""
        (first_row, first_col), (last_row, last_col) = span
        last_row = min(last_row, len(self._view) - 1)
        if first_row >= len(self._view):
            return ""
        lines: List[str] = []
        for index in range(first_row, last_row + 1):
            text = row_text(self._view[index])
            low = _cells_to_index(text, first_col) if index == first_row else 0
            high = _cells_to_index(text, last_col) if index == last_row else len(text)
            lines.append(text[low:high].rstrip() if index != last_row else text[low:high])
        return "\n".join(lines)

    def _select_word(self, row: int, cell: int) -> None:
        if row >= len(self._view):
            return
        text = row_text(self._view[row])
        if not text:
            return
        index = min(_cells_to_index(text, cell), len(text) - 1)
        if text[index].isspace():
            return
        def word_char(char: str) -> bool:
            return char.isalnum() or char in "_-./\\:~@#"
        low = high = index
        while low > 0 and word_char(text[low - 1]):
            low -= 1
        while high < len(text) and word_char(text[high]):
            high += 1
        if high == low:
            high = low + 1
        self.anchor = (row, text_width(text[:low]))
        self.caret = (row, text_width(text[:high]))

    def _select_line(self, row: int) -> None:
        if row >= len(self._view):
            return
        text = row_text(self._view[row])
        self.anchor = (row, 0)
        self.caret = (row, max(1, text_width(text.rstrip())))

    def _finish_selection(self) -> None:
        text = self.selected_text()
        if text.strip() and copy_text(text):
            self.copied(len(text))

    def mouse(self, event: MouseEvent) -> bool:
        """Handle a mouse event; True when something on the screen has to be redrawn."""
        kind = event.event_type
        row, cell = max(0, event.position.y), max(0, event.position.x)
        if kind == MouseEventType.SCROLL_UP:
            self.scroll(3)
            return True
        if kind == MouseEventType.SCROLL_DOWN:
            self.scroll(-3)
            return True
        if event.button not in (MouseButton.LEFT, MouseButton.NONE) and kind != MouseEventType.MOUSE_UP:
            return False
        if kind == MouseEventType.MOUSE_DOWN:
            now = time.monotonic()
            same = abs(row - self._click_cell[0]) <= 0 and abs(cell - self._click_cell[1]) <= 1
            self._clicks = self._clicks + 1 if (same and now - self._click_at < 0.45) else 1
            self._click_at, self._click_cell = now, (row, cell)
            self._press = (row, cell)
            self.anchor = self.caret = (row, cell)
            return True
        if kind == MouseEventType.MOUSE_MOVE:
            if self.anchor is None or self._press is None:
                return False
            if (row, cell) == self.caret:
                return False
            self.caret = (row, cell)
            return True
        if kind == MouseEventType.MOUSE_UP:
            press, self._press = self._press, None
            if press is None:
                return False
            if press == (row, cell) or (press[0] == row and abs(press[1] - cell) <= 0):
                self.anchor = self.caret = None
                if self._clicks >= 3:
                    self._select_line(row)
                    self._finish_selection()
                    return True
                if self._clicks == 2:
                    self._select_word(row, cell)
                    if self.selection() is not None:
                        self._finish_selection()
                    return True
                self._click(row)
                return True
            self.caret = (row, cell)
            self._finish_selection()
            return True
        return False

    def _click(self, row: int) -> None:
        if row >= len(self._visible):
            return
        block, index = self._visible[row]
        if block.on_click is not None:
            block.on_click()
            return
        if block.expandable:
            self.toggle(block, row, index)

    def toggle(self, block: Block, row: int = -1, index: int = 0) -> None:
        """Fold or unfold a block; the row that was clicked keeps its place on the screen."""
        expanded = block.is_expanded(self.expand_all)
        before = len(block.rows(self.width, self.expand_all, self.theme))
        block.expanded = not expanded
        block.update()
        after = len(block.rows(self.width, self.expand_all, self.theme))
        if row >= 0:
            below_before = before - index - 1
            index = min(index, after - 1) if after else 0
            below_after = after - index - 1
            self.offset = max(0, self.offset + below_after - below_before)
        self.anchor = self.caret = None
        self.changed()

    def toggle_last(self) -> bool:
        """Keyboard: fold or unfold the newest expandable block."""
        for block in reversed(self.blocks):
            if block.expandable:
                self.toggle(block)
                return True
        return False

class TranscriptControl(UIControl):
    def __init__(self, transcript: Transcript, invalidate: Callable[[], None]) -> None:
        self.transcript = transcript
        self.invalidate = invalidate
        transcript.changed = invalidate

    def is_focusable(self) -> bool:
        return False

    def preferred_height(self, width, max_available_height, wrap_lines, get_line_prefix):
        return 0

    def create_content(self, width: int, height: Optional[int]) -> UIContent:
        height = height or self.transcript.height
        rows = self.transcript.viewport(height, width)
        styled = self.transcript.style_row

        def get_line(index: int):
            if 0 <= index < len(rows):
                return styled(index, rows[index])
            return []

        return UIContent(get_line=get_line, line_count=height, show_cursor=False)

    def mouse_handler(self, mouse_event: MouseEvent):
        if self.transcript.mouse(mouse_event):
            self.invalidate()
        return None
