"""Inline dialogs drawn in place of the input box.

One generic :class:`Dialog` covers every question MemuCode asks: permission
prompts, pickers (model, session, checkpoint, theme), confirmations, the
agent's own questions and settings. Keys: ↑/↓ or j/k to move, 1-9 to pick,
Enter to confirm, Esc to cancel, Space to toggle in multi-select (Tab when the list has a
search box, where Space is a letter), typing to filter long lists, Tab to type a free answer when that option exists.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Sequence

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import ANSI, StyleAndTextTuples, to_formatted_text
from prompt_toolkit.mouse_events import MouseEventType

from ..i18n import tr
from .input import fuzzy_score


_UNSET: Any = object()


@dataclass
class Option:
    """One row. Without a ``value`` choosing it returns its label; ``Option("Cancel", None)`` returns ``None`` —
    the same as Esc — which is how the "Done", "Back" and "Cancel" rows are told apart from real choices."""
    label: str
    value: Any = _UNSET
    description: str = ""
    style: str = ""                  # extra style class for the label (e.g. "dialog.danger")
    text_input: bool = False         # choosing it asks for free text
    hint: str = ""                   # right-aligned note ("current", "✓")
    #: Round 33 (r33c-models-lists): a muted note right after the label on every row (the full model id next to
    #: its readable name: «Claude Opus 4.6  aws9-claude-opus-4.6»).
    detail: str = ""

    def __post_init__(self) -> None:
        if self.value is _UNSET:
            self.value = self.label


@dataclass
class Dialog:
    title: str
    options: List[Option]
    body: str = ""                   # ANSI text above the options (diff, command, plan)
    multi: bool = False
    filterable: bool = False
    index: int = 0
    selected: set = field(default_factory=set)
    footer: str = ""
    danger: bool = False
    text_prompt: str = ""
    max_visible: int = 10
    future: Optional[asyncio.Future] = None
    on_change: Optional[Callable[[Any], None]] = None
    tag: str = ""
    body_offset: int = 0
    body_height: int = 15
    tabs: List[str] = field(default_factory=list)
    tab_index: int = 0
    on_tab: Optional[Callable[[int], None]] = None
    inline: bool = False             # description on the same line as the label (command lists)
    guard: bool = False              # a question the agent opened on its own: ignore the first keystrokes
    allow_empty: bool = False        # a multi-select may be confirmed with nothing ticked (it answers [])
    initial_filter: str = ""         # what the search box holds when the picker opens (``/model glm``)
    matcher: Optional[Callable[[str, "Option"], Optional[float]]] = None   # custom search: score or None

    def __post_init__(self) -> None:
        self.opened = time.monotonic()
        self.filter_buffer: Optional[Buffer] = None
        self.text_buffer: Optional[Buffer] = None
        self.text_mode = False
        self.view: List[int] = list(range(len(self.options)))
        self._body_cache: tuple = (None, [])
        self._initial_applied = False

    def armed(self) -> bool:
        """False for a moment after an agent-initiated dialog appears, so keys the user was already typing
        into their message cannot answer a permission question by accident."""
        return not self.guard or time.monotonic() - self.opened > 0.7

    def attach(self, filter_buffer: Buffer, text_buffer: Buffer) -> None:
        """The UI owns the two input buffers; a dialog borrows them while shown."""
        self.filter_buffer = filter_buffer
        self.text_buffer = text_buffer
        filter_buffer.reset()
        text_buffer.reset()
        if self.initial_filter and not self._initial_applied:
            self._initial_applied = True
            filter_buffer.document = Document(self.initial_filter, len(self.initial_filter))

    # ------------------------------------------------------------ state
    def switch_tab(self, index: int) -> None:
        if self.tabs:
            self.tab_index = index % len(self.tabs)
            if self.on_tab:
                self.on_tab(self.tab_index)
            if self.filter_buffer is not None:
                self.filter_buffer.reset()
            self.refilter()

    def refilter(self) -> None:
        self._filtered()

    def set_options(self, options: Sequence[Any]) -> None:
        """Replace the rows of an open picker, keeping the row the pointer is on."""
        current = self.current()
        value = current.value if current is not None else None
        self.options = options_from(options)
        self.selected = set()
        self._filtered()
        if current is None:
            return
        for row, position in enumerate(self.view):
            if self.options[position].value == value:
                self.index = row
                break

    def _filtered(self) -> None:
        query = (self.filter_buffer.text if self.filter_buffer is not None else "").strip()
        if not query:
            self.view = list(range(len(self.options)))
        else:
            scored = []
            for position, option in enumerate(self.options):
                if self.matcher is not None:
                    score = self.matcher(query, option)
                else:
                    score = fuzzy_score(query, f"{option.label} {option.detail} {option.description}")
                if score is not None:
                    scored.append((score, position))
            scored.sort(key=lambda row: -row[0])
            self.view = [position for _score, position in scored]
        self.index = 0

    def current(self) -> Optional[Option]:
        if not self.view:
            return None
        self.index = max(0, min(self.index, len(self.view) - 1))
        return self.options[self.view[self.index]]

    def move(self, delta: int) -> None:
        if self.tag == "document":
            self.body_offset = max(0, min(self.body_offset + delta, max(0, len(self.body.splitlines()) - self.body_height)))
            return
        if self.view:
            self.index = max(0, min(self.index + delta, len(self.view) - 1))
            if self.on_change:
                option = self.current()
                self.on_change(option.value if option else None)

    def click(self, row: int, event: Any) -> None:
        if event.event_type not in (MouseEventType.MOUSE_DOWN, MouseEventType.MOUSE_UP):
            return
        if row != self.index:
            self.index = row
            if self.on_change:
                self.on_change(self.options[self.view[row]].value)
        if event.event_type == MouseEventType.MOUSE_UP:
            if self.multi:
                self.toggle()            # a click ticks the row; Enter confirms the whole list
            else:
                self.choose()

    def resolve(self, value: Any) -> None:
        if self.future is not None and not self.future.done():
            self.future.set_result(value)

    def choose(self) -> None:
        option = self.current()
        if self.multi:
            picked = [self.options[i].value for i in sorted(self.selected)]
            if not picked and option is not None and not self.allow_empty:
                picked = [option.value]          # nothing ticked: Enter takes the row the pointer is on
            self.resolve(picked)
            return
        if option is None:
            return
        if option.text_input:
            self.text_mode = True
            return
        self.resolve(option.value)

    def pick_number(self, number: int) -> bool:
        if 1 <= number <= len(self.view):
            self.index = number - 1
            if self.multi:
                self.toggle()
            else:
                self.choose()
            return True
        return False

    def toggle(self) -> None:
        if not self.multi or not self.view:
            return
        position = self.view[self.index]
        if position in self.selected:
            self.selected.discard(position)
        else:
            self.selected.add(position)

    def submit_text(self) -> None:
        option = self.current()
        text = (self.text_buffer.text if self.text_buffer is not None else "").strip()
        if not text:
            self.text_mode = False
            return
        self.resolve({"value": option.value if option else None, "text": text})

    # ------------------------------------------------------------ drawing
    def render_head(self, width: int) -> StyleAndTextTuples:
        """A rule with the title in it — the same language as the input box's rules, no half-drawn frame."""
        style = "class:dialog.danger" if self.danger else "class:dialog.border"
        title = _fit(self.title, max(10, width - 8))
        used = 3 + len(title) + 2
        out: StyleAndTextTuples = [(style, "── "), ("class:dialog.title", title),
                                   (style, " " + "─" * max(0, width - used)), ("", "\n")]
        if self.tabs:
            out.append(("", " "))
            for index, label in enumerate(self.tabs):
                def click(event, index=index):
                    if event.event_type == MouseEventType.MOUSE_UP:
                        self.switch_tab(index)
                out.append(("class:dialog.tab.selected" if index == self.tab_index else "class:dialog.hint",
                            f" {label} ", click))
                out.append(("", " "))
            out.append(("", "\n\n"))
        if self.body:
            # Parsing the ANSI text is the costly part of a frame: do it once per body and scroll position.
            key = (self.body, self.body_offset, self.body_height) if self.tag == "document" else (self.body,)
            if self._body_cache[0] != key:
                body = self.body.rstrip("\n")
                if self.tag == "document":
                    body = "\n".join(body.splitlines()[self.body_offset:self.body_offset + self.body_height])
                self._body_cache = (key, list(to_formatted_text(ANSI(body))))
            out.extend(self._body_cache[1])
            out.append(("", "\n"))
        return out

    def render_options(self, width: int) -> StyleAndTextTuples:
        out: StyleAndTextTuples = []
        if not self.options or (self.text_mode and len(self.options) == 1):
            return []                                       # a plain text field has nothing to choose from
        if not self.view:
            return [("class:dialog.hint", "  nothing matches\n")]
        start = max(0, min(self.index - self.max_visible // 2, len(self.view) - self.max_visible))
        end = min(len(self.view), start + self.max_visible)
        if start > 0:
            out.append(("class:dialog.hint", f"   ↑ {start}\n"))
        label_width = 0
        if self.inline:
            label_width = min(max((len(self.options[self.view[row]].label) for row in range(start, end)), default=0),
                              max(16, width // 2 - 6))
        for row in range(start, end):
            option = self.options[self.view[row]]
            active = row == self.index
            pointer = "❯ " if active else "  "
            number = f"{row + 1}. " if row < 9 and not self.filterable else "   " if not self.filterable else ""
            mark = ""
            if self.multi:
                mark = "◉ " if self.view[row] in self.selected else "○ "
            label_style = "class:dialog.option.selected" if active else ("class:" + option.style if option.style
                                                                         else "class:dialog.option")
            click = lambda event, row=row: self.click(row, event)
            out.append(("class:dialog.option.selected" if active else "", " " + pointer, click))
            out.append(("class:dialog.hint", number, click))
            if self.inline:
                room = max(10, width - label_width - 12)
                out.append((label_style, mark + _fit(option.label, label_width).ljust(label_width), click))
                if option.description:
                    out.append(("class:dialog.option.desc", "  " + _fit(option.description, room), click))
                out.append(("", "\n"))
                continue
            out.append((label_style, mark + option.label, click))
            if option.detail:
                room = max(0, width - len(mark + option.label) - len(option.hint) - 12)
                if room >= 8:
                    out.append(("class:dialog.option.desc", "  " + _fit(option.detail, room), click))
            if option.hint:
                out.append(("class:dialog.hint", "  " + option.hint, click))
            out.append(("", "\n"))
            if option.description and (active or len(self.view) <= 6):
                for line in option.description.splitlines()[:3]:
                    out.append(("class:dialog.option.desc", "       " + line[:max(10, width - 8)] + "\n", click))
        if end < len(self.view):
            out.append(("class:dialog.hint", f"   ↓ {len(self.view) - end}\n"))
        return out

    def render_foot(self, width: int) -> StyleAndTextTuples:
        hints = []
        if self.text_mode:
            hints.append(tr("Enter — отправить · Esc — назад", "Enter to send · Esc to go back"))
        else:
            if self.multi:
                hints.append(tr("Tab — отметить", "Tab to tick") if self.filterable else tr("Tab или пробел — отметить", "Tab or space to tick"))
            hints.append(tr("Enter — выбрать", "Enter to select"))
            if self.filterable:
                hints.append(tr("печатайте для поиска", "type to filter"))
            hints.append(tr("Esc — отмена", "Esc to cancel"))
        text = self.footer or " · ".join(hints)
        return [("class:dialog.hint", "  " + _fit(text, max(10, width - 4)))]


def _fit(text: str, width: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[:max(1, width - 1)] + "…"


def permission_options(request: Any) -> List[Option]:
    rows = [Option(tr("Да", "Yes"), "yes")]
    if request.suggestion and request.suggestion_label:
        rows.append(Option(request.suggestion_label, "always"))
    rows.append(Option(tr("Нет, и сказать MemuCode, что сделать иначе", "No, and tell MemuCode what to do differently"),
                       "feedback", text_input=True))
    rows.append(Option(tr("Нет (esc)", "No (esc)"), "no"))
    return rows


def yes_no(yes: str = "", no: str = "") -> List[Option]:
    return [Option(yes or tr("Да", "Yes"), True), Option(no or tr("Нет", "No"), False)]


def options_from(values: Sequence[Any]) -> List[Option]:
    return [value if isinstance(value, Option) else Option(str(value), value) for value in values]
