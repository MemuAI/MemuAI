"""The interactive full-screen terminal interface of MemuCode.

Layout (redrawn by prompt_toolkit):

    scrollable transcript              ← blocks: messages, folded activity, thoughts
    ● streaming answer tail …          ← live area
    ⠋ Bash(npm test)  ⎿ running lines…
    ✻ Thinking… (12s · ↓ 1.2k · esc to interrupt)
      ⎿ ☐ todo list
    ──────────────────────────────────   ← input box (or a dialog)
     > type here
    ──────────────────────────────────
      /commands completion list
      ⏵⏵ mode · hints            model · ctx 12% · $0.03

Finished blocks are kept in the transcript and rendered lazily for the current
width. Gray summary lines unfold when clicked (Ctrl+O unfolds all). The mouse
wheel and PageUp/PageDown scroll it independently of the bottom composer.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

# prompt_toolkit rings the terminal bell on its own (a key with nothing to do, an empty
# search): on Windows that is the system "ding". MemuCode makes a sound only when the
# "notifications" setting is "bell", and then writes it itself.
os.environ.setdefault("PROMPT_TOOLKIT_BELL", "false")

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.enums import EditingMode
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import ANSI, StyleAndTextTuples, to_formatted_text
from prompt_toolkit.layout.utils import explode_text_fragments
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import (ConditionalContainer, Float, FloatContainer, HSplit, Layout, VSplit, Window,
                                   WindowAlign)
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.processors import Processor, Transformation
from prompt_toolkit.output.color_depth import ColorDepth
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.selection import SelectionType
from rich.text import Text

from .. import VERSION, paths
from ..clipboard import copy_text
from ..i18n import tr
from ..permissions import mode_explain, mode_icon, mode_title
from ..tokens import RateMeter, chunk_tokens, compact_number
from ..triage import request_text, worth_looking_at
from ..ui_base import BaseUI
from . import dialogs as dlg
from .activity import Step
from .input import Attachments, MemuCompleter, PromptHistory, clipboard_image, create_input, image_from_path, pasted_paths
from .render import Renderer, live_preview, split_stable, strip_ansi, tool_title
from .theme import color_depth, palette_for, pt_style
from .transcript import Block, Transcript, TranscriptControl

# Claude-style asterisk pulse. U+2733 (the eight-spoked asterisk) is left out on purpose: Windows Terminal
# draws it as a green emoji tile, which used to flash in the middle of the animation.
SPINNER = ["·", "✢", "*", "✶", "✻", "✽", "✻", "✶", "*", "✢"]
VERBS = ["Thinking", "Pondering", "Cogitating", "Brewing", "Crafting", "Noodling", "Musing", "Scheming",
         "Tinkering", "Percolating", "Ruminating", "Conjuring", "Deliberating", "Synthesizing", "Untangling",
         "Mulling", "Hatching", "Spelunking", "Computing", "Wrangling"]
TIPS = ["Shift+Tab cycles modes: accept edits, plan, auto.", "Press Esc twice to go back to an earlier message.",
        "!command runs in the shell, # note saves a rule.", "@file attaches a file to your message.",
        "Alt+V pastes an image from the clipboard.", "/btw asks a side question without interrupting.",
        "/compact shrinks the history, /context shows how full it is.",
        "Click a gray summary line to see exactly what was done; Ctrl+O unfolds all.",
        "/subtask runs a task in parallel while you keep talking.", "/goal keeps working until the goal is met."]


#: The "?" shortcut panel. Round 33: a module constant, so the built-in MemuCode guide (selfdoc.py) lists exactly these.
SHORTCUT_ROWS = (
    ("/", "commands"), ("@", "mention a file"),
    ("!", "bash mode"), ("#", "save a memory"),
    ("shift+tab", "permission mode"), ("esc esc", "rewind"),
    ("shift+enter / alt+enter / \\⏎", "new line"),
    ("ctrl+r", "history search"), ("ctrl+g", "external editor"),
    ("ctrl+v / alt+v", "paste text or an image"), ("ctrl+o", "unfold all details"),
    ("ctrl+t", "todo list"), ("alt+p", "switch model"),
    ("alt+t", "toggle thinking"), ("alt+r", "show / hide the model's thinking"), ("ctrl+l", "clear screen"),
    ("ctrl+c ×2 / ctrl+d", "exit"),
)


# ---------------------------------------------------------------- processors

class _Tokens(Processor):
    PATTERN = re.compile(r"\[Image #\d+\]|\[File #\d+: [^\]]+\]|\[Pasted text #\d+ \+\d+ lines\]|(?<![\w@])@(?:\"[^\"]+\"|[^\s@]+)")

    def apply_transformation(self, ti):
        fragments = explode_text_fragments(ti.fragments)
        line = ti.document.lines[ti.lineno] if ti.lineno < len(ti.document.lines) else ""
        spans = [(m.start(), m.end()) for m in self.PATTERN.finditer(line)]
        if ti.lineno == 0:
            command = re.match(r"^/\S+", line)
            if command:
                spans.append((0, command.end()))
        for start, end in spans:
            for index in range(start, min(end, len(fragments))):
                style, char = fragments[index][0], fragments[index][1]
                fragments[index] = (style + " class:brand", char)
        return Transformation(fragments)


class _Placeholder(Processor):
    def __init__(self, text) -> None:
        self.text = text

    def apply_transformation(self, ti):
        if ti.lineno == 0 and not ti.document.text:
            return Transformation(list(ti.fragments) + [("class:placeholder", self.text())])
        return Transformation(ti.fragments)


def _ellipsize(text: str, cells: int) -> str:
    """Cut a one-line status to the window width with «…» — never let the live area wrap mid-word (round 34)."""
    from prompt_toolkit.utils import get_cwidth
    if cells <= 1:
        return ""
    if get_cwidth(text) <= cells:
        return text
    out, used = [], 0
    for char in text:
        size = get_cwidth(char)
        if used + size > cells - 1:
            break
        out.append(char)
        used += size
    return "".join(out).rstrip() + "…"


def fit_meta(meta: List[str], cells: int) -> str:
    """« (12s · ↓ 1k tokens · … · esc to interrupt)» that fits ``cells`` cells.

    Round 34: on a narrow window the spinner line wrapped as «esc to interr / upt)». Optional parts go first
    (from the one before «esc to interrupt» backwards; the clock stays), then «esc to interrupt», then a hard cut."""
    from prompt_toolkit.utils import get_cwidth
    parts = list(meta)

    def render(items: List[str]) -> str:
        return " (" + " · ".join(items) + ")" if items else ""

    while len(parts) > 2 and get_cwidth(render(parts)) > cells:
        del parts[-2]
    if len(parts) == 2 and get_cwidth(render(parts)) > cells:
        parts = parts[:1]
    line = render(parts)
    return line if get_cwidth(line) <= cells else _ellipsize(line, cells)


class _MouseListControl(FormattedTextControl):
    """Keep wheel events inside a picker instead of selecting terminal text."""

    def __init__(self, text, scroll, after_click=None, **kwargs):
        super().__init__(text, **kwargs)
        self._scroll = scroll
        self._after_click = after_click

    def mouse_handler(self, mouse_event):
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            self._scroll(-3)
            return None
        if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            self._scroll(3)
            return None
        result = super().mouse_handler(mouse_event)
        if self._after_click and mouse_event.event_type == MouseEventType.MOUSE_UP:
            self._after_click()
        return result


class Menu:
    """The suggestion list of the input box: commands, files, arguments.

    It is the interface's own state rather than the buffer's ``complete_state``: prompt_toolkit drops
    that on every keystroke and refills it through an executor, one suggestion per hop, so the list
    used to vanish and come back while typing. Here nothing is selected until the arrows are used and
    moving through the list never rewrites the text being typed.
    """

    __slots__ = ("items", "index", "text", "kind")

    def __init__(self, items: List[Any], text: str, kind: str, index: Optional[int] = None) -> None:
        self.items = items
        self.text = text            # the text before the cursor these items were found for
        self.kind = kind
        self.index = index

    @property
    def current(self) -> Any:
        if not self.items:
            return None
        return self.items[self.index if self.index is not None else 0]

    def move(self, delta: int) -> None:
        if not self.items:
            return
        if self.index is None:
            self.index = 0 if delta > 0 else len(self.items) - 1
        else:
            self.index = (self.index + delta) % len(self.items)


class _SnappyApplication(Application):
    """An Application whose lone Escape acts at once.

    Escape is also the first half of every Alt chord, so prompt_toolkit waits ``timeoutlen`` after it
    to see whether another key follows — closing a dialog took that long. A chord arrives as one
    batch (the console reports Alt+V as a single event; a terminal sends ``ESC v`` in one write), so an
    Escape that is still alone once the batch has been processed is a real Escape: do not wait.
    """

    @property
    def timeoutlen(self) -> Optional[float]:
        processor = getattr(self, "key_processor", None)
        buffer = processor.key_buffer if processor is not None else ()
        if len(buffer) == 1 and buffer[0].key == Keys.Escape:
            return 0.0
        return self._timeoutlen

    @timeoutlen.setter
    def timeoutlen(self, value: Optional[float]) -> None:
        self._timeoutlen = value


class _CopyOnSelectBufferControl(BufferControl):
    def __init__(self, *args, copied, clicked=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._copied = copied
        self._clicked = clicked

    def mouse_handler(self, mouse_event):
        result = super().mouse_handler(mouse_event)
        selected = ""
        if mouse_event.event_type == MouseEventType.MOUSE_UP and self.buffer.selection_state:
            selected = self.buffer.copy_selection().text
            if selected and copy_text(selected):
                self._copied(len(selected))
        if mouse_event.event_type == MouseEventType.MOUSE_UP and not selected and self._clicked:
            self._clicked()
        return result


# ---------------------------------------------------------------- the UI

class TerminalUI(BaseUI):
    interactive = True

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        runtime.ui = self
        self.settings = runtime.settings
        self.palette = palette_for(self.settings.get("theme"), self.settings.get("accent"))
        self.depth = color_depth()
        self.renderer = Renderer(self.palette, self.depth)
        self.renderer.width_of = self.width
        self.attachments = Attachments()
        self.history = PromptHistory(paths.project_key(runtime.root))
        self.queue: List[Dict[str, Any]] = []
        self.busy = False
        self.busy_started = 0.0
        self.task: Optional[asyncio.Task] = None
        self.side_tasks: List[asyncio.Task] = []
        self._scheduled_running: set = set()          # tasks of /schedule that this window has started
        self._schedule_checked = 0.0
        self.verb = ""
        self.status_text = ""
        self.stream = ""
        self.stream_committed = 0
        self.stream_bullet = True
        self.reasoning = ""
        self.reasoning_started = 0.0
        self.reasoning_printed = False
        self.mode_toast: Optional[tuple] = None             # (text, until) shown in the status line after a mode change
        self.live_thinking = bool(self.settings.get("show_thinking"))   # the thinking text under the spinner
        self.turn_out_tokens = 0.0              # estimated tokens written during this turn (all its requests)
        self.rate = RateMeter()                 # speed of the request in progress; see tokens.RateMeter
        # What is going on with the request right now, for the line under the answer: waiting for the
        # provider, its pings, the first token, a tool call being written.
        self.stream_phase = ""                  # request | accepted | (empty: tokens are arriving)
        self.stream_started_at = 0.0
        self.first_token_at = 0.0
        self.pings = 0
        self.last_ping = 0.0
        self.block_what = ""                    # "Write(src/App.jsx)" while the model writes a hidden tool block
        self.block_chars = 0
        self._block_counted = 0
        self._streamers: Dict[str, bool] = {}   # model → does it stream? (learned from how its answers arrive)
        self.active_tools: Dict[str, Any] = {}
        self.dialogs: List[dlg.Dialog] = []
        self.show_help = False
        self.show_todos = bool(self.settings.get("todo_panel", True))
        self.transcript = Transcript(self._copied)
        self.verbose = bool(self.settings.get("verbose"))
        self.exit_armed = 0.0
        self.last_escape = 0.0
        self.search_query: Optional[str] = None
        self.search_hit: Optional[tuple] = None
        self.tip = random.choice(TIPS)
        self.statusline_cache = ("", 0.0)
        self.context_cache: tuple = (-1, 0.0, 0)
        self._context_refreshing = False
        self._cost_cache = (0.0, 0.0)
        self.app: Optional[Application] = None
        self.copied_count = 0
        self.copied_until = 0.0
        self.activity_block: Optional[Block] = None       # the open "what the agent did" group
        self.activity_steps: List[Step] = []
        self.last_kind = ""                               # kind of the newest transcript block
        self.connecting = False
        self._auto_resumes = 0
        self.clipboard_image_seen = False
        self.menu: Optional[Menu] = None
        self._menu_text: Optional[str] = None             # the text the list was last built for
        self._menu_serial = 0
        self._menu_closed_for: Optional[str] = None       # Esc hid the list for exactly this text
        self._live_cache: Dict[str, Any] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.exit_code = 0
        from ..commands import CommandRegistry
        self.commands = CommandRegistry(self)
        from ..autocontinue import AutoContinue
        self.auto_continue = AutoContinue(self)            # Round 33: /auto-continue
        self._build()

    # ------------------------------------------------------------ building
    @property
    def verbose(self) -> bool:
        """Unfold every folded block (Ctrl+O, /settings → verbose output)."""
        return self.transcript.expand_all

    @verbose.setter
    def verbose(self, value: bool) -> None:
        self.transcript.set_expand_all(bool(value))

    def width(self) -> int:
        try:
            if self.app is not None:
                return max(30, self.app.output.get_size().columns)
        except Exception:
            pass
        return max(30, shutil.get_terminal_size((100, 30)).columns)

    def _build(self) -> None:
        # Suggestions are the interface's own (see Menu): the buffer has no completer at all.
        # ``runtime.files`` is replaced by /cd, so the completer asks for it each time instead of binding it.
        self.completer = MemuCompleter(
            self.commands.items, lambda: self.runtime.files.files(sync=False),
            lambda: list(self.runtime.agents()), lambda: self.runtime.root,
            lambda: self.runtime.router.cached_models(), self._session_ids)
        self.input_buffer = Buffer(multiline=True, on_text_changed=self._text_changed,
                                   on_cursor_position_changed=lambda _buffer: self._refresh_menu())
        self.dialog_filter = Buffer(multiline=False, on_text_changed=lambda _b: self._dialog_refilter())
        self.dialog_text = Buffer(multiline=True)
        has_dialog = Condition(lambda: bool(self.dialogs))
        text_mode = Condition(lambda: bool(self.dialogs) and self.dialogs[0].text_mode)
        filterable = Condition(lambda: bool(self.dialogs) and self.dialogs[0].filterable and not self.dialogs[0].text_mode)

        live = ConditionalContainer(Window(FormattedTextControl(self._live), wrap_lines=True, dont_extend_height=True),
                                    filter=Condition(self._has_live))
        self.transcript_window = Window(TranscriptControl(self.transcript, self.invalidate),
                                        height=Dimension(weight=1), wrap_lines=False, always_hide_cursor=True)
        self.input_window = Window(
            _CopyOnSelectBufferControl(self.input_buffer,
                                       input_processors=[_Tokens(), _Placeholder(self._placeholder)],
                                       focus_on_click=True, copied=self._copied, clicked=self._edit_clicked_paste),
            wrap_lines=True, height=Dimension(min=1, max=12), dont_extend_height=True,
            get_line_prefix=self._prompt_prefix)
        box = HSplit([
            Window(FormattedTextControl(lambda: self._border("top")), height=1),
            self.input_window,
            Window(FormattedTextControl(lambda: self._border("bottom")), height=1),
        ])
        self.dialog_window = Window(_MouseListControl(self._dialog_options, self._dialog_scroll, self._focus_dialog, focusable=True), wrap_lines=True,
                                    dont_extend_height=True)
        self.filter_window = Window(_CopyOnSelectBufferControl(self.dialog_filter, copied=self._copied,
                                                              focus_on_click=True), height=1,
                                    get_line_prefix=lambda n, w: [("class:dialog.hint", "  search: ")])
        self.text_window = Window(_CopyOnSelectBufferControl(self.dialog_text, copied=self._copied,
                                                            focus_on_click=True),
                                  height=lambda: Dimension(min=1, max=self._text_rows()),
                                  dont_extend_height=True, wrap_lines=True,
                                  get_line_prefix=lambda n, w: [("class:dialog.border", "  │ ")])
        dialog = HSplit([
            Window(_MouseListControl(self._dialog_head, self._dialog_scroll, self._focus_dialog),
                   wrap_lines=True, dont_extend_height=True),
            ConditionalContainer(self.filter_window, filter=filterable),
            self.dialog_window,
            ConditionalContainer(self.text_window, filter=text_mode),
            Window(FormattedTextControl(lambda: self.dialogs[0].render_foot(self.width()) if self.dialogs else []),
                   dont_extend_height=True),
        ])
        completions = ConditionalContainer(
            Window(_MouseListControl(self._completion_list, self._completion_scroll), wrap_lines=False,
                   dont_extend_height=True),
            filter=Condition(lambda: bool(self.menu and self.menu.items)) & ~has_dialog)
        # One window for both halves: two half-width windows clipped the left part mid-word and glued
        # it to the model name ("● 2 agentssol-gpt-…"). fit_footer() makes them share the row instead.
        footer = ConditionalContainer(Window(FormattedTextControl(self._footer_line), height=1),
                                      filter=~has_dialog)
        # The suggestion list floats over the bottom rows of the transcript. As a row of the layout it
        # pushed the whole transcript up and down on every keystroke that changed its length, and the
        # terminal had to repaint the full screen each time.
        upper = FloatContainer(HSplit([self.transcript_window, live]),
                               floats=[Float(completions, left=0, right=0, bottom=0)])
        root = HSplit([upper, ConditionalContainer(dialog, filter=has_dialog),
                       ConditionalContainer(box, filter=~has_dialog), footer])
        self.layout = Layout(root, focused_element=self.input_window)
        self.key_bindings = self._bindings(has_dialog, text_mode, filterable)

    def make_app(self, *, input: Any = None, output: Any = None) -> Application:
        depth = {"truecolor": ColorDepth.TRUE_COLOR, "256": ColorDepth.DEPTH_8_BIT,
                 "none": ColorDepth.DEPTH_1_BIT}.get(self.depth, ColorDepth.TRUE_COLOR)
        # ``max_render_postpone_time=None`` matters on Windows: with a value, prompt_toolkit re-queues its
        # render callback while the (Proactor) loop has anything else pending, which is always — every
        # keystroke then waited the whole postpone time while the loop spun at full speed.
        app = _SnappyApplication(layout=self.layout, key_bindings=self.key_bindings, style=pt_style(self.palette),
                          full_screen=True, mouse_support=True,
                          color_depth=depth,
                          input=input if input is not None else create_input(), output=output,
                          erase_when_done=False, min_redraw_interval=0.008, max_render_postpone_time=None,
                          editing_mode=EditingMode.VI if self.settings.get("vim_mode") else EditingMode.EMACS)
        app.ttimeoutlen = 0.03
        app.timeoutlen = 0.12
        return app

    # ------------------------------------------------------------ small views
    def _transcript_height(self) -> int:
        info = self.transcript_window.render_info
        return info.window_height if info is not None else max(1, self._rows() - 7)

    def _copied(self, count: int) -> None:
        self.copied_count = count
        self.copied_until = time.monotonic() + 3
        self.invalidate()

    def _completion_context(self) -> bool:
        return bool(self.completer.kind(self.input_buffer.document.text_before_cursor))

    def _session_ids(self) -> List[str]:
        from ..session import list_sessions
        return [row["id"] for row in list_sessions(self.runtime.root, limit=40)]

    def _text_changed(self, _buffer: Buffer) -> None:
        if self.show_help and self.input_buffer.text not in ("", "?"):
            self.show_help = False
        if self.exit_armed:
            self.exit_armed = 0.0
        self._menu_closed_for = None
        self._refresh_menu()

    # ------------------------------------------------------------ suggestions
    def _refresh_menu(self, *, force: bool = False) -> None:
        """Bring the suggestion list in line with the text before the cursor.

        Commands and fixed arguments are matched right here, in well under a millisecond. File names
        and folders (the disk, tens of thousands of names) are matched on a worker thread; only the
        newest request is shown, and the previous list stays on screen until it arrives.
        """
        text = self.input_buffer.document.text_before_cursor
        if not force and text == self._menu_text:
            return
        self._menu_text = text
        self._menu_serial += 1
        kind = self.completer.kind(text) if self.app is not None and self.app.is_running else ""
        if not kind or (not force and self._menu_closed_for == text):
            self.menu = None
            return
        document = self.input_buffer.document
        if kind in ("command", "argument"):
            self.menu = Menu(list(self.completer.get_completions(document)), text, kind)
            return
        if self.menu is not None and self.menu.kind != kind:
            self.menu = None
        serial = self._menu_serial
        loop = asyncio.get_running_loop()

        def work() -> None:
            try:
                found = list(self.completer.get_completions(document, None, lambda: serial != self._menu_serial))
            except Exception:
                found = []
            if serial == self._menu_serial:
                loop.call_soon_threadsafe(self._menu_ready, serial, Menu(found, text, kind))

        loop.run_in_executor(_suggestion_workers(), work)

    def _menu_ready(self, serial: int, menu: Menu) -> None:
        if serial == self._menu_serial:
            self.menu = menu
            self.invalidate()

    def _close_menu(self) -> None:
        """Esc: hide the list until the text changes."""
        self._menu_closed_for = self.input_buffer.document.text_before_cursor
        self._menu_serial += 1
        self.menu = None

    def _apply_suggestion(self, completion: Any) -> bool:
        """Put a suggestion into the text. False when it is already there (nothing changed)."""
        buffer = self.input_buffer
        before = buffer.document.text_before_cursor
        span = self.completer.span(before)
        if before[len(before) - span:] == completion.text:
            return False
        start = len(before) - span
        buffer.save_to_undo_stack()
        # One change, so the list is rebuilt once (for the text as it ends up).
        buffer.document = Document(before[:start] + completion.text + buffer.document.text_after_cursor,
                                   start + len(completion.text))
        return True

    def _placeholder(self) -> str:
        if self.busy:
            return tr("Можно писать дальше — сообщение встанет в очередь", "Keep typing — it will be queued")
        if self.runtime.session.messages:
            return ""
        return tr(' Попробуйте «объясни, как устроен этот проект»', ' Try "explain how this project is organized"')

    def _input_mode(self) -> str:
        text = self.input_buffer.text
        if text.startswith("!"):
            return "bash"
        if text.startswith("#") and not text.startswith("##"):
            return "memory"
        return self.runtime.permissions.mode

    def _border_style(self) -> str:
        mode = self._input_mode()
        if mode in ("bash", "memory", "acceptEdits", "plan", "auto", "bypassPermissions"):
            return f"class:mode.{mode.lower()}"
        return "class:border"

    def _border(self, which: str) -> StyleAndTextTuples:
        width = self.width()
        style = self._border_style()
        return [(style, "─" * width)]

    def _prompt_prefix(self, lineno: int, wrap: int) -> StyleAndTextTuples:
        if lineno == 0 and wrap == 0:
            mode = self._input_mode()
            mark = {"bash": "! ", "memory": "# "}.get(mode, "> ")
            style = f"class:mode.{mode}" if mode in ("bash", "memory") else "class:prompt"
            return [(style, " " + mark)]
        return [("", "   ")]

    def _has_live(self) -> bool:
        return bool(self.busy or self.active_tools or self.queue or self.show_help or self.search_query is not None
                    or self.connecting or (self.show_todos and self._open_todos()) or self._background_rows())

    def _open_todos(self) -> List[Dict[str, Any]]:
        todos = self.runtime.session.todos or []
        return todos if any(todo.get("status") != "completed" for todo in todos) else []

    # ------------------------------------------------------------ live area
    def _tail_fragments(self, tail: str, width: int) -> StyleAndTextTuples:
        """The unfinished end of the streaming answer, rendered at most ~25 times a second."""
        key = (width, self.stream_bullet, id(self.renderer.theme))
        cached = self._live_cache.get("tail")
        now = time.monotonic()
        if cached and cached[0] == key and (cached[1] == tail or now - cached[3] < 0.04):
            if cached[1] != tail:
                self._refresh_soon()
            return cached[2]
        preview = live_preview(tail, max_lines=max(6, min(18, self._rows() - 12)))
        rendered = self.renderer.assistant_chunk(preview, self.stream_bullet, width)
        fragments = [(style, text) for style, text, *_rest in to_formatted_text(ANSI(rendered))]
        self._live_cache["tail"] = (key, tail, fragments, now)
        return fragments

    def _refresh_soon(self) -> None:
        """One redraw a moment from now (the streamed tail changed while it was being rate-limited)."""
        if self._live_cache.get("timer") or self.app is None or not self.app.is_running:
            return

        def fire() -> None:
            self._live_cache["timer"] = None
            self.invalidate()

        try:
            self._live_cache["timer"] = asyncio.get_running_loop().call_later(0.045, fire)
        except RuntimeError:
            pass

    def _live(self) -> StyleAndTextTuples:
        out: StyleAndTextTuples = []
        width = self.width()
        tail = self.stream[self.stream_committed:]
        if self.busy and tail.strip():
            out.extend(self._tail_fragments(tail, width))
        now = time.monotonic()
        for call in list(self.active_tools.values()):
            frame = SPINNER[int(now * 6) % len(SPINNER)]
            out.append(("class:dim", f"{frame} "))
            out.append(("bold", tool_title(call.name)))
            if call.display:
                out.append(("class:dim", f"({call.display[:max(10, width - 24)]})"))
            elapsed = now - call.started if call.started else 0
            if elapsed > 2:
                out.append(("class:subtle", f"  {elapsed:.0f}s"))
            out.append(("", "\n"))
            lines = "".join(call.live).splitlines()[-4:]
            for index, line in enumerate(lines):
                prefix = "  ⎿  " if index == 0 else "     "
                out.append(("class:dim", (prefix + line)[:width - 1] + "\n"))
        if self.busy and not self.dialogs:
            out.extend(self._spinner_line(width))
        elif self.connecting and not self.dialogs:
            out.extend(self._connecting_line())
        if self.show_todos:
            todos = self._open_todos()
            for position, todo in enumerate(todos[:12]):
                status = todo.get("status")
                mark = {"completed": "☒", "in_progress": "◼", "pending": "☐"}.get(status, "☐")
                style = {"completed": "class:todo.done", "in_progress": "class:todo.active"}.get(status, "class:todo.pending")
                label = todo.get("activeForm") if status == "in_progress" and todo.get("activeForm") else todo.get("content")
                lead = "  ⎿  " if position == 0 else "     "
                out.append(("class:subtle", lead))
                out.append((style, f"{mark} {label}"[:max(10, width - 8)] + "\n"))
            if len(todos) > 12:
                out.append(("class:dim", f"       … +{len(todos) - 12} more\n"))
        for position, row in enumerate(self._background_rows()[:4]):
            out.append(("class:subtle", "  ⎿  " if position == 0 else "     "))
            out.append(("class:info", "◦ "))
            out.append(("class:dim", row[:max(10, width - 10)] + "\n"))
        for item in self.queue[:6]:
            text = str(item.get("display") or item.get("content") or "").replace("\n", " ")
            out.append(("class:queued", f"  ↳ {text[:width - 6]}\n"))
        if self.queue:
            out.append(("class:subtle", "    " + tr("в очереди · ↑ — изменить", "queued · ↑ to edit") + "\n"))
        if self.search_query is not None:
            hit = self.search_hit[1].replace("\n", " ⏎ ") if self.search_hit else ""
            out.append(("class:search", f"  (reverse-i-search)`{self.search_query}': "))
            out.append(("", hit[:width - 30] + "\n"))
        if self.show_help:
            out.extend(self._shortcuts(width))
        return out

    def _connecting_line(self) -> StyleAndTextTuples:
        frame = SPINNER[int(time.monotonic() * 8) % len(SPINNER)]
        return [("class:spinner", f"{frame} "), ("class:dim", "Connecting to MemuAI Proxy and models…\n")]

    def _rows(self) -> int:
        try:
            return self.app.output.get_size().rows if self.app else 30
        except Exception:
            return 30

    @staticmethod
    def _clock(seconds: float) -> str:
        return f"{int(seconds)}s" if seconds < 60 else f"{int(seconds // 60)}m {int(seconds % 60)}s"

    def _phase_label(self, now: float) -> str:
        """What the interface is waiting for, in a few words — never just a random verb while nothing happens."""
        tail = self.stream[self.stream_committed:].strip()
        if self.active_tools:
            return self.verb
        if self.block_what and not tail:
            return f"Preparing {self.block_what}"
        if self.reasoning and not tail:
            return "Thinking"
        if tail:
            return self.verb
        if self.connecting:
            return "Connecting"
        if self.stream_phase == "request":
            return "Sending the request" if now - self.stream_started_at < 1.2 else "Waiting for the model"
        if self.stream_phase == "accepted":
            return "Waiting for the model"
        return self.verb

    def _spinner_line(self, width: int) -> StyleAndTextTuples:
        now = time.monotonic()
        frame = SPINNER[int(now * 9) % len(SPINNER)]
        text = f"{self._phase_label(now)}…"
        shine = int(now * 12) % (len(text) + 8) - 4
        toggle = self._click(self._toggle_thinking) if self.reasoning or self.stream_phase else None

        def piece(style: str, chunk: str) -> tuple:
            return (style, chunk, toggle) if toggle is not None else (style, chunk)

        out: StyleAndTextTuples = [piece("class:spinner", f"{frame} ")]
        for index, char in enumerate(text):
            out.append(piece("class:spinner.shine" if abs(index - shine) <= 1 else "class:spinner.text", char))
        elapsed = now - self.busy_started
        meta = [self._clock(elapsed)]
        tokens = int(self.turn_out_tokens)
        if tokens:
            meta.append(f"↓ {compact_number(tokens)} tokens")
            speed = self.rate.rate(now)
            if speed is not None and tokens >= 20:
                meta.append(f"{speed:.0f} tok/s")
        elif self.block_chars and self.block_what:
            meta.append(f"{compact_number(self.block_chars)} characters")
        waited = now - self.stream_started_at if self.stream_started_at else 0
        if not self.first_token_at and self.stream_phase and waited >= 3 and not self.active_tools:
            if self.pings:
                meta.append(f"provider is working · {self.pings} ping{'s' if self.pings != 1 else ''}")
            elif self.stream_phase == "accepted":
                meta.append("request accepted")
            if waited >= 25:
                meta.append("slow start — Esc cancels")
        if self.status_text:
            meta.append(self.status_text)
        if self.reasoning and not self.live_thinking:
            meta.append("click or alt+r: see thinking")
        meta.append("esc to interrupt")
        out.append(("class:spinner.meta", fit_meta(meta, width - 1 - 2 - len(text))))
        out.append(("", "\n"))
        if self.live_thinking:
            out.extend(self._thinking_view(width))
        if self.settings.get("spinner_tips", True) and elapsed > 8 and not self.active_tools and not self.live_thinking:
            out.append(("class:subtle", _ellipsize("  ⎿  Tip: " + self.tip, width - 1) + "\n"))
        return out

    THINKING_ROWS = 10

    def _thinking_view(self, width: int) -> StyleAndTextTuples:
        """The end of what the model is thinking, under the spinner. Click the spinner (or alt+r) to hide it."""
        text = self.reasoning.strip()
        close = self._click(self._toggle_thinking)
        out: StyleAndTextTuples = [("class:thinking.head", "  ▾ Thinking", close),
                                   ("class:subtle", f" · {compact_number(round(chunk_tokens(text)))} tokens · click to hide\n", close)]
        if not text:
            out.append(("class:subtle", "      …no thinking text yet (some models and providers do not send it)\n"))
            return out
        rows: List[str] = []
        for line in text[-6000:].splitlines():
            line = line.rstrip()
            if not line:
                continue
            while len(line) > width - 8:
                rows.append(line[:width - 8])
                line = line[width - 8:]
            rows.append(line)
        for row in rows[-self.THINKING_ROWS:]:
            out.append(("class:thinking", "    " + row + "\n"))
        return out

    def _toggle_thinking(self) -> None:
        """Show or hide the model's thinking under the spinner (and open or fold the finished blocks' default)."""
        self.live_thinking = not self.live_thinking
        if self.live_thinking and not self.reasoning and self.busy and not self.first_token_at:
            self.status_text = "no thinking text yet"
        self.invalidate()

    def _shortcuts(self, width: int) -> StyleAndTextTuples:
        rows = SHORTCUT_ROWS
        out: StyleAndTextTuples = []
        column = max(len(key) for key, _ in rows) + 2
        text_width = max(len(text) for _key, text in rows) + 2
        per_row = 2 if width >= 2 * (column + text_width + 2) + 2 else 1
        for index in range(0, len(rows), per_row):
            for key, text in rows[index:index + per_row]:
                out.append(("class:brand", "  " + key.ljust(column)))
                out.append(("class:dim", text.ljust(text_width) if per_row > 1 else text))
            out.append(("", "\n"))
        return out

    def _completion_list(self) -> StyleAndTextTuples:
        menu = self.menu
        if not menu or not menu.items:
            return []
        completions = menu.items
        # Nothing is highlighted until an arrow is pressed, but Enter and Tab take the first row,
        # so that row is the one drawn as current.
        index = menu.index if menu.index is not None else 0
        visible = 8
        start = max(0, min(index - visible // 2, len(completions) - visible))
        width = self.width()
        name_width = min(max(12, width - 4), max(len(c.display_text) for c in completions[start:start + visible]) + 2)
        out: StyleAndTextTuples = []
        for position in range(start, min(len(completions), start + visible)):
            completion = completions[position]
            active = position == index
            style = "class:completion.selected" if active else "class:completion"
            def click(event, row=position):
                if event.event_type == MouseEventType.MOUSE_DOWN:
                    menu.index = row
                elif event.event_type == MouseEventType.MOUSE_UP:
                    self._apply_suggestion(completions[row])
                    if re.fullmatch(r"/\S+", self.input_buffer.text):
                        self.input_buffer.insert_text(" ")
                return None
            used = 2 + name_width
            out.append((style, "  " + completion.display_text[:name_width].ljust(name_width), click))
            meta = completion.display_meta_text
            if meta and width - name_width - 6 >= 12:
                meta = meta[:width - name_width - 6]
                used += len(meta)
                out.append(("class:completion.meta.selected" if active else "class:completion.meta", meta, click))
            out.append(("class:completion", " " * max(0, width - used), click))      # a full row: covers the transcript under it
            out.append(("", "\n"))
        if len(completions) > visible:
            counter = f"  {index + 1}/{len(completions)}"
            out.append(("class:subtle", counter.ljust(width) + "\n"))
        return out

    def _click(self, action):
        """A mouse handler for a footer fragment: runs ``action`` on release."""
        def handler(event):
            if event.event_type == MouseEventType.MOUSE_UP:
                action()
            return None
        return handler

    def _footer_line(self) -> StyleAndTextTuples:
        return fit_footer(self._footer_left(), self._footer_right(), self.width())

    def _footer_left(self) -> StyleAndTextTuples:
        if self.copied_count and time.monotonic() < self.copied_until:
            return [("class:ok", f"  Copied {self.copied_count} characters")]
        if self.exit_armed and time.monotonic() - self.exit_armed < 1.6:
            return [("class:warn", "  Press Ctrl+C again to exit")]
        if self.transcript.offset:
            return [("class:info", "  ↓ Scrolled up — click here or press End to see the latest",
                     self._click(self.transcript.scroll_to_end))]
        if self.clipboard_image_seen and not self.input_buffer.text:
            return [("class:info", "  ▣ Image in the clipboard — press Alt+V (or click here) to attach it",
                     self._click(lambda: asyncio.ensure_future(self.paste_image())))]
        if self.menu is not None and self.menu.kind == "mention":
            note = self.runtime.files.describe()
            if note:
                return [("class:subtle", "  " + note)]
        mode = self._input_mode()
        if mode == "bash":
            return [("class:mode.bash", "  ! bash mode")]
        if mode == "memory":
            return [("class:mode.memory", "  # memory mode: Enter to choose where to save")]
        permission = self.runtime.permissions.mode
        parts: StyleAndTextTuples = []
        cycle = self._click(lambda: self.runtime.permissions.cycle(source="keys"))
        toast = getattr(self, "mode_toast", None)
        if toast and time.monotonic() < toast[1]:
            parts.append((f"class:mode.{permission.lower()}", f"  {mode_icon(permission)}{toast[0]}", cycle))
        else:
            parts.append((f"class:mode.{permission.lower()}", f"  {mode_icon(permission)}{mode_title(permission)}", cycle))
            if permission == "default":
                parts.append(("class:subtle", " · shift+tab to change · ? for shortcuts", cycle))
            else:
                parts.append(("class:subtle", " (shift+tab to change)", cycle))
        goal = self.runtime.session.meta.get("goal")
        if isinstance(goal, dict) and goal.get("text") and not goal.get("done"):
            state = "paused" if goal.get("paused") else f"{goal.get('continuations', 0)}/{goal.get('max', 10)}"
            parts.append(("class:brand", f"  ◎ goal {state}"))
        auto_continue = self.auto_continue.footer() if getattr(self, "auto_continue", None) is not None else ""
        if auto_continue:                                   # Round 33: /auto-continue countdown
            parts.append(("class:brand", "  " + auto_continue,
                          self._click(lambda: self.run_command_text("/auto-continue status"))))
        agents, commands = self.runtime.jobs.running(), self.runtime.shells.running()
        if agents or commands:
            label = ", ".join(part for part in (
                f"{agents} agent{'s' if agents != 1 else ''}" if agents else "",
                f"{commands} command{'s' if commands != 1 else ''}" if commands else "") if part)
            parts.append(("class:info", f"  ● {label} running · /tasks",
                          self._click(lambda: self.run_command_text("/tasks"))))
        return parts

    def _toggle_help(self) -> None:
        self.show_help = not self.show_help
        self.invalidate()

    METRICS = (("cost", "Cost at API prices"), ("tokens", "Tokens: input / output"), ("total", "Tokens: total"),
               ("context", "Context window used"), ("lines", "Lines changed (+added −removed)"),
               ("duration", "Session time"), ("tools", "Tool calls"), ("requests", "Model requests"),
               ("off", "Nothing"))

    def metric_text(self, metric: str = "") -> str:
        """The status-line number chosen in /settings (or by clicking it)."""
        runtime = self.runtime
        preview = bool(metric)
        metric = metric or str(self.settings.get("footer_metric", "cost"))
        if metric == "cost":
            if not preview and not self.settings.get("pricing.show_in_footer", True):
                return ""
            stamp, cost = self._cost_cache
            if time.monotonic() - stamp > 2:
                cost = runtime.stats.costs(self.settings.get("pricing.overrides") or {})["api_equivalent"]
                self._cost_cache = (time.monotonic(), cost)
            from ...pricing import format_usd
            return format_usd(cost)
        usage = runtime.stats.total()
        if metric == "tokens":
            return f"{compact_number(usage.input)} in / {compact_number(usage.output)} out"
        if metric == "total":
            return f"{compact_number(usage.input + usage.output)} tokens"
        if metric == "context":
            share = self._context_share()
            window = runtime.effective_window()
            return f"{compact_number(int(share * window))} / {compact_number(window)}"
        if metric == "lines":
            return f"+{runtime.stats.lines_added} −{runtime.stats.lines_removed}"
        if metric == "duration":
            minutes = int((time.time() - runtime.stats.started) / 60)
            return f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60} min"
        if metric == "tools":
            return f"{runtime.stats.tool_calls} tool calls"
        if metric == "requests":
            return f"{usage.requests} requests"
        return ""

    async def choose_metric(self) -> None:
        current = str(self.settings.get("footer_metric", "cost"))
        options = [dlg.Option(label, key, self._metric_preview(key), hint="✓" if key == current else "")
                   for key, label in self.METRICS]
        picked = await self.select("Show in the status line", options,
                                   index=next((i for i, (key, _l) in enumerate(self.METRICS) if key == current), 0))
        if picked:
            self.settings.set("footer_metric", picked)
            self.invalidate()

    def _metric_preview(self, key: str) -> str:
        return "now: " + self.metric_text(key) if key != "off" else "hides the number"

    def _footer_right(self) -> StyleAndTextTuples:
        custom = self._custom_statusline()
        if custom is not None:
            return [("class:footer", custom + "  ")]
        runtime = self.runtime
        model = runtime.model or "no model"
        model = model if len(model) <= 34 else "…" + model[-33:]
        share = self._context_share()
        style = "class:warn" if share >= 0.8 else "class:footer"
        out: StyleAndTextTuples = [(style, model, self._click(lambda: self.run_command_text("/model")))]
        if share:
            out.append((style, f"  ·  ctx {share:.0%}", self._click(lambda: self.run_command_text("/context"))))
        branch = _git_branch(runtime.root)
        if branch:
            out.append((style, "  ·  ⎇ " + branch))
        metric = self.metric_text()
        out.append((style, "  ·  " + (metric or "metric off") + "  ", self._click(lambda: asyncio.ensure_future(self.choose_metric()))))
        return out

    def _context_share(self) -> float:
        # The share only changes with the conversation (or the model): it is not recomputed on a
        # timer. Counting a long session every few seconds held the interpreter and made typing stutter.
        messages = (len(self.runtime.session.messages), self.runtime.model)
        cached_count, cached_share, stamp = self.context_cache
        if cached_count == messages and (not self.busy or time.monotonic() - stamp < 20):
            return cached_share
        if not self._context_refreshing and self.app is not None and self.app.is_running:
            self._context_refreshing = True
            async def refresh():
                try:
                    from ..compact import context_breakdown
                    share = await asyncio.to_thread(lambda: context_breakdown(self.runtime)["share"])
                    self.context_cache = (messages, share, time.monotonic())
                except Exception:
                    self.context_cache = (messages, cached_share, time.monotonic())
                finally:
                    self._context_refreshing = False
                    self.invalidate()
            self.side_tasks.append(asyncio.create_task(refresh()))
        return cached_share

    def _custom_statusline(self) -> Optional[str]:
        config = self.settings.get("statusline") or {}
        if not isinstance(config, dict) or config.get("type") != "command" or not config.get("command"):
            return None
        text, stamp = self.statusline_cache
        if time.monotonic() - stamp > 3 and not getattr(self, "_statusline_running", False):
            self._statusline_running = True
            asyncio.get_event_loop().run_in_executor(None, self._run_statusline, str(config["command"]))
        return text

    def _run_statusline(self, command: str) -> None:
        try:
            from ..tools.shell import argv_for
            runtime = self.runtime
            costs = runtime.stats.costs()
            payload = json.dumps({
                "session_id": runtime.session.id, "version": VERSION, "cwd": str(runtime.root),
                "model": {"id": runtime.model, "display_name": runtime.model},
                "workspace": {"current_dir": str(runtime.session.cwd or runtime.root), "project_dir": str(runtime.root)},
                "cost": {"total_cost_usd": round(costs["api_equivalent"], 6),
                         "total_duration_ms": int((time.time() - runtime.stats.started) * 1000),
                         "total_lines_added": runtime.stats.lines_added,
                         "total_lines_removed": runtime.stats.lines_removed},
                "permission_mode": runtime.permissions.mode,
            })
            # Bounded for the whole process tree: with subprocess.run a script that left a child holding
            # stdout hung this thread forever on Windows, and the status line never refreshed again.
            from ... import code_sandbox
            _code, out, _err, _late = code_sandbox.run_bounded(argv_for(self.settings, command),
                                                                input_bytes=payload.encode("utf-8"),
                                                                cwd=str(runtime.root), timeout=4)
            line = out.decode("utf-8", "replace").strip().splitlines()
            self.statusline_cache = (strip_ansi(line[0])[:160] if line else "", time.monotonic())
        except Exception:
            self.statusline_cache = ("", time.monotonic())
        finally:
            self._statusline_running = False
        self.invalidate()

    # ------------------------------------------------------------ dialogs
    def _dialog_head(self) -> StyleAndTextTuples:
        if not self.dialogs:
            return []
        dialog = self.dialogs[0]
        dialog.body_height = max(3, self._rows() - 10)
        return dialog.render_head(self.width())

    async def open_document(self, title: str, body: str, *, loader: Any = None) -> None:
        """A scrollable text window. ``loader(set_body)`` may keep filling it while it is open, so a
        window never waits for the network before it appears."""
        dialog = dlg.Dialog(title, [dlg.Option("Close", "close")], body=body,
                            tag="document", footer="Wheel / ↑ ↓ / PgUp PgDn to scroll · Enter / Esc to close")
        task = None
        if loader is not None:
            def set_body(text: str) -> None:
                dialog.body = text
                self.invalidate()

            async def fill() -> None:
                try:
                    await loader(set_body)
                except Exception:
                    self.runtime.log_error(traceback.format_exc())

            task = asyncio.ensure_future(fill())
        try:
            await self.open_dialog(dialog)
        finally:
            if task is not None:
                task.cancel()

    def _text_rows(self) -> int:
        """Rows of the free-text field: a pasted text is edited in a roomy one."""
        if self.dialogs and self.dialogs[0].tag == "paste":
            return max(6, min(18, self._rows() - 8))
        return 6

    def _dialog_options(self) -> StyleAndTextTuples:
        return self.dialogs[0].render_options(self.width()) if self.dialogs else []

    def _dialog_scroll(self, delta: int) -> None:
        if self.dialogs:
            self.dialogs[0].move(delta)
            self.invalidate()

    def _completion_scroll(self, delta: int) -> None:
        if self.menu:
            self.menu.move(1 if delta > 0 else -1)
            self.invalidate()

    def _dialog_refilter(self) -> None:
        if self.dialogs:
            self.dialogs[0].refilter()

    def _focus_dialog(self) -> None:
        # The layout exists before the application runs: a dialog shown at startup takes the keys from the start.
        if not self.dialogs:
            return
        dialog = self.dialogs[0]
        try:
            if dialog.text_mode:
                self.layout.focus(self.text_window)
            elif dialog.filterable:
                self.layout.focus(self.filter_window)
            else:
                self.layout.focus(self.dialog_window)
        except ValueError:
            pass

    def present_dialog(self, dialog: dlg.Dialog) -> None:
        """Show ``dialog`` now, without waiting for the answer (:meth:`open_dialog` waits).

        Works before :meth:`run` too: the startup questions are presented before the terminal reads any key,
        so an Enter typed while MemuCode is still starting answers them instead of the hidden prompt."""
        dialog.future = asyncio.get_running_loop().create_future()
        self.dialogs.append(dialog)
        if len(self.dialogs) == 1:
            dialog.attach(self.dialog_filter, self.dialog_text)
            self._focus_dialog()
        self.invalidate()

    async def open_dialog(self, dialog: dlg.Dialog) -> Any:
        if dialog not in self.dialogs:
            self.present_dialog(dialog)
        try:
            return await dialog.future
        finally:
            if dialog in self.dialogs:
                self.dialogs.remove(dialog)
            if self.dialogs:
                self.dialogs[0].attach(self.dialog_filter, self.dialog_text)
                self._focus_dialog()
            else:
                try:
                    self.layout.focus(self.input_window)
                except ValueError:
                    pass
            self.invalidate()

    async def select(self, title: str, options: List[Any], *, body: str = "", multi: bool = False,
                     filterable: Optional[bool] = None, index: int = 0, danger: bool = False,
                     footer: str = "", max_visible: int = 10, updater: Any = None, initial_filter: str = "",
                     matcher: Any = None, selected: Optional[List[Any]] = None, allow_empty: bool = False) -> Any:
        """A picker. ``updater(dialog)`` may run while it is open and replace its rows
        (``dialog.set_options``): a list is shown from what is already known and refreshed in place.
        ``initial_filter`` fills the search box, ``matcher(query, option)`` replaces the search, ``selected``
        pre-ticks rows (by value) of a multi-select, ``allow_empty`` lets it be confirmed with nothing ticked."""
        items = dlg.options_from(options)
        dialog = dlg.Dialog(title=title, options=items, body=body, multi=multi, danger=danger, footer=footer,
                            filterable=(len(items) > 12 or bool(initial_filter)) if filterable is None else filterable,
                            index=max(0, min(index, len(items) - 1)) if items else 0, max_visible=max_visible,
                            initial_filter=initial_filter, matcher=matcher, allow_empty=allow_empty)
        if selected:
            wanted = list(selected)
            dialog.selected = {position for position, option in enumerate(items) if option.value in wanted}
        if self.app is None or not self.app.is_running:
            return None
        task = None
        if updater is not None:
            async def refresh() -> None:
                try:
                    await updater(dialog)
                except Exception:
                    self.runtime.log_error(traceback.format_exc())
                self.invalidate()

            task = asyncio.ensure_future(refresh())
        try:
            return await self.open_dialog(dialog)
        finally:
            if task is not None:
                task.cancel()

    async def prompt_text(self, title: str, *, body: str = "", default: str = "", tag: str = "") -> Optional[str]:
        option = dlg.Option(tr("Ввести…", "Type…"), "text", text_input=True)
        dialog = dlg.Dialog(title=title, options=[option], body=body, tag=tag,
                            footer="Enter for newline · Ctrl+S to save · Esc to cancel" if tag == "paste" else "")
        dialog.text_mode = True
        if self.app is None or not self.app.is_running:
            return None
        loop = asyncio.get_running_loop()
        dialog.future = loop.create_future()
        self.dialogs.append(dialog)
        if len(self.dialogs) == 1:
            dialog.attach(self.dialog_filter, self.dialog_text)
            if default:
                self.dialog_text.document = Document(default, 0 if tag == "paste" else len(default))
            self._focus_dialog()
        self.invalidate()
        try:
            result = await dialog.future
        finally:
            if dialog in self.dialogs:
                self.dialogs.remove(dialog)
            if self.dialogs:
                self._focus_dialog()
            elif self.app is not None:
                self.app.layout.focus(self.input_window)
            self.invalidate()
        if isinstance(result, dict):
            return str(result.get("text") or "")
        return None

    # ------------------------------------------------------------ printing
    def invalidate(self) -> None:
        if self.app is not None and self.app.is_running:
            self.app.invalidate()

    def add_block(self, source: Any, *, kind: str = "notice", **kwargs: Any) -> Block:
        """Append one block to the transcript. Anything but a tool summary closes the open summary."""
        if kind != "activity":
            self.activity_block = None
            self.activity_steps = []
        self.last_kind = kind
        return self.transcript.add(Block(source, kind=kind, **kwargs))

    def write(self, ansi: str) -> None:
        if ansi:
            self.add_block(ansi, kind="notice")

    def print(self, *items: Any) -> None:
        self.add_block(lambda width, expanded, items=items: self.renderer.render(*items, width=width), kind="notice")

    def print_markdown(self, text: str) -> None:
        self.add_block(lambda width, expanded: self.renderer.render(self.renderer.md(text), width=width), kind="notice")

    def say(self, text: str, level: str = "info") -> None:
        self.add_block(lambda width, expanded: self.renderer.notice(text, level, width), kind="notice")

    def show_user(self, text: str, *, images: int = 0, kind: str = "prompt", queued: bool = False) -> None:
        """The user's message on its gray band (long ones fold; click to unfold)."""
        long = text.count("\n") + 1 > 14
        self.add_block(lambda width, expanded: self.renderer.user(text, images=images, kind=kind, queued=queued,
                                                                 width=width, expanded=expanded),
                       kind="user", expandable=long)
        if not queued:
            self.transcript.scroll_to_end()

    def add_assistant(self, text: str, first: bool) -> None:
        text = text.strip("\n")
        if not text.strip():
            return
        lead = "\n" if first else ""
        self.add_block(lambda width, expanded: lead + self.renderer.assistant_chunk(text, first, width), kind="assistant")

    # ------------------------------------------------------------ BaseUI: agent events
    def status(self, text: str) -> None:
        self.status_text = text[:80]
        self.invalidate()

    def mode_changed(self, mode: str) -> None:
        """The permission mode changed (keys, a command, the plan, or another window): say what it means now."""
        permissions = self.runtime.permissions
        source = getattr(permissions, "change_source", "command")
        text = f"{mode_title(mode)} — {mode_explain(mode)}"
        self.mode_toast = (text, time.monotonic() + (6.0 if source == "keys" else 4.0))
        if source == "sync":
            self.say(tr(f"Другое окно MemuCode сменило режим разрешений: {mode_title(mode)}. {mode_explain(mode)}.",
                        f"Another MemuCode window changed the permission mode: {mode_title(mode)}. {mode_explain(mode)}."),
                     "warn" if mode == "bypassPermissions" else "info")
        elif source in ("command", "plan"):
            self.say(tr(f"Режим разрешений: {mode_title(mode)} — {mode_explain(mode)}",
                        f"Permission mode: {mode_title(mode)} — {mode_explain(mode)}"),
                     "warn" if mode == "bypassPermissions" else "info")
        self.invalidate()
        asyncio.get_event_loop().call_later(6.2, self.invalidate)

    def notice(self, text: str, level: str = "info") -> None:
        self.say(text, level)

    def compacted(self, result: Dict[str, Any]) -> None:
        before, after = result.get("before", 0), result.get("after", 0)
        self.say(f"History compacted: {compact_number(before)} → {compact_number(after)} tokens "
                 f"({result.get('summarized', 0)} messages summarized)", "success")

    def turn_started(self, agent: Any, message: Dict[str, Any]) -> None:
        if message.get("queued"):
            text = str(message.get("shown") or message.get("display") or message.get("content") or "")
            self.show_user(text)

    def stream_started(self, agent: Any) -> None:
        if getattr(agent, "depth", 0):
            return
        self.stream = ""
        self.stream_committed = 0
        self.stream_bullet = True
        self.reasoning = ""
        self.reasoning_started = 0.0
        self.reasoning_printed = False
        self.status_text = ""
        self.stream_phase = "request"
        self.stream_started_at = time.monotonic()
        self.first_token_at = 0.0
        self.pings = 0
        self.last_ping = 0.0
        self.block_what = ""
        self.block_chars = 0
        self._block_counted = 0
        self.rate.reset()

    def stream_status(self, agent: Any, phase: str, data: Dict[str, Any]) -> None:
        """Progress of the request itself: sent, accepted by the provider, a ping while it works."""
        if getattr(agent, "depth", 0):
            return
        now = time.monotonic()
        if phase == "accepted":
            if self.stream_phase == "request":
                self.stream_phase = "accepted"
        elif phase == "alive":
            self.pings += 1
            self.last_ping = now
        self.invalidate()

    def stream_block(self, agent: Any, what: str, chars: int) -> None:
        """The model is writing a tool call. The call itself stays hidden; its size and target show."""
        if getattr(agent, "depth", 0):
            return
        self._mark_first_token()
        self.block_what = what or self.block_what
        if chars:
            self.block_chars = chars
            if chars > self._block_counted:                  # hidden text counts toward the token estimate
                piece = (chars - self._block_counted) / 3.8
                self.turn_out_tokens += piece
                self.rate.add(piece, time.monotonic())
                self._block_counted = chars
        self.invalidate()

    def _count_output(self, text: str) -> None:
        """A piece of the answer (or of the thinking) arrived: it counts toward the turn's tokens and the speed."""
        piece = chunk_tokens(text)
        self.turn_out_tokens += piece
        self.rate.add(piece, time.monotonic())

    def _mark_first_token(self) -> None:
        if not self.first_token_at:
            self.first_token_at = time.monotonic()
            self.stream_phase = ""

    def stream_reasoning(self, agent: Any, text: str) -> None:
        if getattr(agent, "depth", 0):
            return
        self._mark_first_token()
        if not self.reasoning_started:
            self.reasoning_started = time.monotonic()
        self.reasoning += text
        self._count_output(text)
        self.invalidate()

    def _flush_reasoning(self) -> None:
        if self.reasoning and not self.reasoning_printed:
            seconds = time.monotonic() - self.reasoning_started if self.reasoning_started else 0
            # Open when the user was watching it live (or asked for it in the settings): it must not vanish
            # the moment the answer starts.
            text, full = self.reasoning, self.live_thinking or bool(self.settings.get("show_thinking"))
            self.add_block(lambda width, expanded: self.renderer.thinking(text, seconds, full=full, expanded=expanded,
                                                                          width=width),
                           kind="thinking", expandable=bool(text.strip()))
            self.reasoning_printed = True

    def stream_text(self, agent: Any, text: str) -> None:
        if getattr(agent, "depth", 0):
            return
        self._mark_first_token()
        self._flush_reasoning()
        self.stream += text
        self._count_output(text)
        pending = self.stream[self.stream_committed:]
        stable, _tail = split_stable(pending)
        if stable.strip():
            self.add_assistant(stable, self.stream_bullet)
            self.stream_bullet = False
            self.stream_committed += len(stable)
        elif stable:
            self.stream_committed += len(stable)
        self.invalidate()

    def _note_stream_style(self, agent: Any) -> None:
        """A model that sends its whole answer in one piece gives nothing to show while it works: say so once, so
        the wait is understood and not mistaken for a hang."""
        if not self.first_token_at:
            return
        now = time.monotonic()
        waited = self.first_token_at - self.stream_started_at
        spread = now - self.first_token_at
        chars = len(self.stream) + len(self.reasoning) + self.block_chars
        model = str(getattr(agent, "model", "") or "")
        if chars >= 300 and spread < 0.8 and waited >= 6:
            if self._streamers.get(model) is not False:
                self._streamers[model] = False
                self.say(tr(f"{model} присылает ответ целиком, не по частям — пока он работал, показывать было нечего "
                            f"(ожидание {waited:.0f} с). Это нормально для этой модели.",
                            f"{model} sends its answer all at once instead of streaming it, so there was nothing to "
                            f"show while it worked ({waited:.0f}s wait). That is normal for this model."))
        elif spread >= 1.5:
            self._streamers[model] = True

    def stream_finished(self, agent: Any, text: str, reasoning: str, usage: Any) -> None:
        if getattr(agent, "depth", 0):
            return
        self._note_stream_style(agent)
        self._flush_reasoning()
        self._commit_tail()
        self.status_text = ""
        self.invalidate()

    def _commit_tail(self) -> None:
        rest = self.stream[self.stream_committed:]
        if rest.strip():
            self.add_assistant(rest, self.stream_bullet)
            self.stream_bullet = False
        self.stream = ""
        self.stream_committed = 0

    def tool_started(self, agent: Any, call: Any) -> None:
        if not call.started:
            call.started = time.monotonic()
        self.active_tools[call.id] = call
        self.invalidate()

    def tool_progress(self, agent: Any, call: Any, text: str) -> None:
        if call is None:
            return
        call.live.append(text)
        if len(call.live) > 300:
            del call.live[:-200]
        self.invalidate()

    def tool_finished(self, agent: Any, call: Any, result: Any) -> None:
        """A finished call joins the open gray summary of this stretch of work (or opens a new one)."""
        self.active_tools.pop(call.id, None)
        seconds = (call.finished or time.monotonic()) - call.started if call.started else 0.0
        self.add_step(Step(call, result, seconds))

    def add_step(self, step: Step) -> None:
        self.activity_steps.append(step)
        if self.activity_block is None:
            steps = self.activity_steps
            lead = "" if self.last_kind in ("assistant", "thinking", "activity") else "\n"
            self.activity_block = self.add_block(
                lambda width, expanded: lead + self.renderer.activity(steps, expanded, width),
                kind="activity", expandable=True)
        else:
            self.activity_block.update()
            self.transcript.changed()
        self.invalidate()

    def todos_changed(self, todos: List[Dict[str, Any]]) -> None:
        self.runtime.session.save_todos()
        self.invalidate()

    def turn_finished(self, agent: Any, result: Any) -> None:
        if getattr(agent, "depth", 0):
            return
        self._commit_tail()
        from ..tools.base import ToolResult
        for call in list(self.active_tools.values()):
            call.status = "cancelled"
            self.add_step(Step(call, ToolResult(ok=False, summary="stopped"), 0.0))
        self.active_tools.clear()
        if result.stopped == "interrupted":
            self.write(self.renderer.render(Text("  ⎿  Interrupted · What should MemuCode do instead?", style="err")))
        elif result.stopped == "error" and result.error:
            # Round 32: a classified model error (app/cli/errors.py) leads with what happened in plain words.
            head, _sep, rest = result.error.partition("\n")
            if getattr(result, "error_category", "") and rest:
                self.write(self.renderer.error(head, rest))
            else:
                self.write(self.renderer.error("Error", result.error))
        elif result.stopped == "limit":
            self.say("Round limit reached — the agent summarised.", "warn")
        elif result.stopped == "stuck" and result.error and not (result.text or "").strip():
            # Round 29: «Stop» on the stuck pause ended the turn — say so instead of falling silent.
            self.say(result.error + " — send a new message to continue.", "warn")
        try:
            # Round 33: settle an /auto-continue try, or offer the command under a limit error.
            self.auto_continue.turn_finished(result)
        except Exception:
            self.runtime.log_error(traceback.format_exc())
        self.activity_block = None
        self.activity_steps = []

    def background_finished(self, job: Dict[str, Any]) -> None:
        level = "success" if job["status"] == "done" else "warn"
        who = f"{job.get('name')} " if job.get("name") else ""
        self.say(f"Agent #{job['id']} {who}{job['status']}: {job.get('description') or job['prompt'][:60]} — "
                 f"/tasks {job['id']}", level)
        self._notify("Background task finished", job["prompt"][:120])
        self.maybe_wake()

    def maybe_wake(self) -> None:
        """An idle MemuCode that started background work itself picks the results up and carries on."""
        runtime = self.runtime
        if self.busy or self.dialogs or not runtime.wake_requested() or self._auto_resumes >= 20:
            return
        self._auto_resumes += 1
        content = "\n\n".join(runtime.drain_notes())
        self.say("Background work finished — MemuCode continues")
        self.start(self.run_agent(content, display="↻ background results", kind="command"))

    def _background_rows(self) -> List[str]:
        """One line per running background agent or command, for the live area."""
        rows: List[str] = []
        now = time.time()
        for job in self.runtime.jobs.jobs:
            if job["status"] == "running":
                # Round 32: one live line per agent — name, role, progress, what it does right now.
                seconds = int(now - job["started"])
                extra = f" — {job['last']}" if job.get("last") else ""
                who = job.get("name") or job.get("description") or ""
                role = f" ({job['role'][:40]})" if job.get("role") else ""
                queued = f" · {len(job['inbox'])} follow-up queued" if job.get("inbox") else ""
                rows.append(f"agent #{job['id']} {who}{role} · {job.get('tools', 0)} tools · {seconds}s{queued}{extra}")
        for shell in self.runtime.shells.list():
            if shell["running"]:
                rows.append(f"$ {shell['command'][:60]} · {int(now - shell['started'])}s")
        return rows

    def take_queued(self) -> List[Dict[str, Any]]:
        """Messages typed while the agent worked, for the agent to read between two rounds. Queued commands
        stay where they are: they run after the turn, not inside it."""
        items = [item for item in self.queue if not item.get("command")]
        self.queue = [item for item in self.queue if item.get("command")]
        self.invalidate()
        return items

    # ------------------------------------------------------------ BaseUI: questions
    async def ask_permission(self, request: Any) -> Dict[str, Any]:
        tool = request.tool
        prepared = request.prepared
        self.renderer.width = self.width()
        body_parts: List[str] = []
        if request.reason:
            body_parts.append(self.renderer.render(Text("  " + request.reason, style="warn")))
        if tool.name == "Bash":
            description = str(request.call.args.get("description") or "")
            body_parts.append(self.renderer.render(Text("  " + prepared.preview, style="bold")))
            if description:
                body_parts.append(self.renderer.render(Text("  " + description, style="dim")))
        elif prepared.diff:
            body_parts.append(self.renderer.render(self.renderer.diff(prepared.diff, max_lines=max(10, self._rows() - 18))))
        elif prepared.preview:
            preview = prepared.preview
            lines = preview.splitlines()
            if len(lines) > 16:
                preview = "\n".join(lines[:16]) + f"\n… +{len(lines) - 16}"
            body_parts.append(self.renderer.render(Text(_indent(preview), style="dim")))
        else:
            body_parts.append(self.renderer.render(Text("  " + (request.key or tool.name), style="dim")))
        title = prepared.title or {
            "exec": tr("Выполнить команду?", "Run this command?"),
            "edit": tr("Изменить файл?", "Make this edit?"),
            "web": tr("Открыть страницу?", "Fetch this page?"),
            "mcp": tr("Вызвать инструмент MCP?", "Call this MCP tool?"),
            "automation": tr("Запустить автоматизацию?", "Start automation?"),
        }.get(tool.kind, tr("Разрешить?", "Allow?"))
        title = f"{tool_title(tool.name)} · {title}"
        options = dlg.permission_options(request)
        self._notify(tr("MemuCode ждёт разрешения", "MemuCode needs your permission"), title, only_idle=True)
        dialog = dlg.Dialog(title=title, options=options, body="".join(body_parts), danger=request.danger, guard=True)
        dialog.remote_request = request          # app/cli/remote.py shows the exact call in the Telegram Mini App
        answer = await self.open_dialog(dialog)
        if isinstance(answer, dict):
            return {"choice": "feedback", "feedback": str(answer.get("text") or "")}
        if answer in (None, "no"):
            return {"choice": "stop" if answer is None else "no"}
        return {"choice": answer}

    async def ask_question(self, question: str, options: List[str], multi: bool = False) -> Optional[str]:
        rows = [dlg.Option(option, option) for option in options]
        rows.append(dlg.Option(tr("Свой ответ…", "Type your own answer…"), "__text__", text_input=True))
        dialog = dlg.Dialog(title="MemuCode asks", options=rows, multi=multi and bool(options), guard=True,
                            body=self.renderer.render(self.renderer.md(question)))
        if not options:
            dialog.text_mode = True   # nothing to choose (e.g. «Hint for the model:»): the text field is open at once
        self._notify(tr("MemuCode задаёт вопрос", "MemuCode has a question"), question[:120], only_idle=True)
        answer = await self.open_dialog(dialog)
        if isinstance(answer, dict):
            return str(answer.get("text") or "")
        if isinstance(answer, list):
            return ", ".join(str(item) for item in answer if item != "__text__") or None
        return answer

    async def approve_plan(self, plan: str) -> Dict[str, Any]:
        self.renderer.width = self.width()
        self.write(self.renderer.panel(self.renderer.md(plan), title=tr("План MemuCode", "MemuCode plan"),
                                       style="mode.plan"))
        options = [
            dlg.Option(tr("Да, и принимать правки автоматически", "Yes, and auto-accept edits"), "auto"),
            dlg.Option(tr("Да, но подтверждать каждую правку", "Yes, and manually approve edits"), "manual"),
            dlg.Option(tr("Нет, доработать план…", "No, keep planning…"), "keep", text_input=True),
        ]
        answer = await self.open_dialog(dlg.Dialog(title="Would you like to proceed?", options=options, guard=True))
        if isinstance(answer, dict):
            return {"choice": "keep", "feedback": str(answer.get("text") or "")}
        return {"choice": answer or "keep"}

    async def confirm(self, title: str, body: str = "", danger: bool = False) -> bool:
        """Yes/no. A dangerous question starts on No: Enter alone never destroys or opens up anything."""
        rendered = self.renderer.render(Text(_indent(body), style="dim")) if body else ""
        options = list(reversed(dlg.yes_no())) if danger else dlg.yes_no()
        answer = await self.open_dialog(dlg.Dialog(title=title, options=options, body=rendered, danger=danger, guard=True))
        return answer is True

    # ------------------------------------------------------------ notifications
    def _ring(self) -> None:
        """The terminal bell, written straight to the terminal: the toolkit's own bell is switched
        off (see the top of this module) so that it never dings on its own."""
        try:
            target = sys.__stdout__ or sys.stdout
            target.write("\a")
            target.flush()
        except Exception:
            pass

    def _notify(self, title: str, text: str, *, only_idle: bool = False) -> None:
        mode = str(self.settings.get("notifications") or "auto")
        if mode == "off":
            return
        if only_idle and self.busy and time.monotonic() - self.busy_started < 20:
            return
        # The terminal bell is the Windows "ding": it sounds only when asked for ("bell").
        # The default is a silent toast.
        if mode == "bell":
            self._ring()
        if mode in ("auto", "toast"):
            try:
                from ...desktop_notifications import show_completion
                import threading
                threading.Thread(target=show_completion, args=(f"MemuCode · {title}", text), daemon=True).start()
            except Exception:
                pass

    # ------------------------------------------------------------ key bindings
    def _bindings(self, has_dialog, text_mode, filterable) -> KeyBindings:
        kb = KeyBindings()
        no_dialog = ~has_dialog
        empty = Condition(lambda: not self.input_buffer.text)

        # eager: with a selection, Emacs mode also knows "Ctrl+C >" and would wait for a second key.
        @kb.add("c-c", eager=True)
        def _ctrl_c(event):
            if self.dialogs:
                self.dialogs[0].resolve(None)
                return
            selected = event.current_buffer
            if selected.selection_state and selected.text:
                state = selected.selection_state
                text = selected.copy_selection().text       # Ctrl+C on a selection copies it, like everywhere
                selected.selection_state = state            # …and the selection stays
                if text and copy_text(text):
                    self._copied(len(text))
                return
            if self.busy:
                self.interrupt()
                return
            if self.input_buffer.text:
                self.input_buffer.reset()
                self.attachments.clear()
                return
            if self.exit_armed and time.monotonic() - self.exit_armed < 1.6:
                self.exit()
            else:
                self.exit_armed = time.monotonic()
                asyncio.get_event_loop().call_later(1.7, self.invalidate)

        @kb.add("c-d", filter=no_dialog & empty)
        def _ctrl_d(event):
            self.exit()

        @kb.add("c-a", eager=True)
        def _select_all(event):
            """Select everything in the field being typed into (the message, a filter, a free answer)."""
            buffer = event.current_buffer
            if not buffer.text:
                return
            buffer.cursor_position = 0
            buffer.start_selection(selection_type=SelectionType.CHARACTERS)
            # "Shift mode" is prompt_toolkit's ordinary-editor selection: typing replaces it,
            # an arrow key drops it.
            buffer.selection_state.enter_shift_mode()
            buffer.cursor_position = len(buffer.text)

        @kb.add("c-x", filter=Condition(lambda: bool(self.app and self.app.current_buffer.selection_state)), eager=True)
        def _cut(event):
            buffer = event.current_buffer
            text = buffer.cut_selection().text
            if text and copy_text(text):
                self._copied(len(text))

        @kb.add("c-l")
        def _clear(event):
            self.transcript.clear()
            event.app.invalidate()

        @kb.add("pageup", filter=no_dialog)
        def _scroll_up(event):
            self.transcript.scroll(max(3, self._transcript_height() - 2))

        @kb.add("pagedown", filter=no_dialog)
        def _scroll_down(event):
            self.transcript.scroll(-max(3, self._transcript_height() - 2))

        @kb.add("end", filter=no_dialog & empty & Condition(lambda: self.transcript.offset > 0))
        def _to_end(event):
            self.transcript.scroll_to_end()

        @kb.add("c-t")
        def _todos(event):
            self.show_todos = not self.show_todos

        @kb.add("c-o")
        def _verbose(event):
            self.verbose = not self.verbose
            self.say("Details: all unfolded (Ctrl+O to fold)" if self.verbose else "Details: folded (click a gray line to unfold it)")

        # ---- dialog keys
        @kb.add("up", filter=has_dialog & ~text_mode, eager=True)
        @kb.add("c-p", filter=has_dialog & ~text_mode, eager=True)
        def _up(event):
            self.dialogs[0].move(-1)

        @kb.add("down", filter=has_dialog & ~text_mode, eager=True)
        @kb.add("c-n", filter=has_dialog & ~text_mode, eager=True)
        def _down(event):
            self.dialogs[0].move(1)

        @kb.add("k", filter=has_dialog & ~text_mode & ~filterable, eager=True)
        def _k(event):
            self.dialogs[0].move(-1)

        @kb.add("j", filter=has_dialog & ~text_mode & ~filterable, eager=True)
        def _j(event):
            self.dialogs[0].move(1)

        @kb.add("pageup", filter=has_dialog & ~text_mode, eager=True)
        def _pgup(event):
            self.dialogs[0].move(-8)

        @kb.add("pagedown", filter=has_dialog & ~text_mode, eager=True)
        def _pgdn(event):
            self.dialogs[0].move(8)

        # Ctrl+J too: an Enter typed before the terminal switched to raw mode (while MemuCode was starting)
        # arrives as a line feed, and the first-run questions must not lose it.
        @kb.add("enter", filter=has_dialog & ~text_mode, eager=True)
        @kb.add("c-j", filter=has_dialog & ~text_mode, eager=True)
        def _choose(event):
            dialog = self.dialogs[0]
            if not dialog.armed():
                return
            dialog.choose()
            if dialog.text_mode:
                self._focus_dialog()

        @kb.add("enter", filter=has_dialog & text_mode, eager=True)
        def _submit_text(event):
            if self.dialogs[0].tag == "paste":
                self.dialog_text.insert_text("\n")
                return
            if not self.dialogs[0].armed():
                return
            self.dialogs[0].submit_text()
            self._focus_dialog()

        @kb.add("c-s", filter=has_dialog & text_mode, eager=True)
        def _save_text(event):
            dialog = self.dialogs[0]
            if dialog.tag == "paste":
                dialog.resolve({"value": "text", "text": self.dialog_text.text})

        @kb.add("escape", "enter", filter=has_dialog & text_mode, eager=True)
        def _dialog_newline(event):
            self.dialog_text.insert_text("\n")

        @kb.add("tab", filter=has_dialog & ~text_mode, eager=True)
        def _dialog_tab(event):
            dialog = self.dialogs[0]
            if dialog.tabs:
                dialog.switch_tab(dialog.tab_index + 1)
                self._focus_dialog()
                return
            if dialog.multi:
                # Tab ticks the row and moves on — in a list with a search box Space is a letter of the search, so
                # Tab is the one key that works in every list of ticks.
                if dialog.armed():
                    dialog.toggle()
                    dialog.move(1)
                return
            option = dialog.current()
            if option and option.text_input:
                dialog.text_mode = True
                self._focus_dialog()
            else:
                dialog.move(1)

        @kb.add("left", filter=has_dialog & ~text_mode, eager=True)
        @kb.add("s-tab", filter=has_dialog & ~text_mode, eager=True)
        def _previous_tab(event):
            dialog = self.dialogs[0]
            if dialog.tabs:
                dialog.switch_tab(dialog.tab_index - 1)
                self._focus_dialog()

        @kb.add("right", filter=has_dialog & ~text_mode, eager=True)
        def _next_tab(event):
            dialog = self.dialogs[0]
            if dialog.tabs:
                dialog.switch_tab(dialog.tab_index + 1)
                self._focus_dialog()

        @kb.add(" ", filter=has_dialog & ~text_mode & ~filterable, eager=True)
        def _space(event):
            if self.dialogs[0].armed():
                self.dialogs[0].toggle()

        # In a list with a search box Space ticks a row while nothing is typed yet (a space cannot start a search).
        @kb.add(" ", filter=has_dialog & filterable & Condition(
            lambda: self.dialogs[0].multi and not self.dialog_filter.text), eager=True)
        def _space_in_search(event):
            if self.dialogs[0].armed():
                self.dialogs[0].toggle()

        for digit in "123456789":
            @kb.add(digit, filter=has_dialog & ~text_mode & ~filterable, eager=True)
            def _digit(event, digit=digit):
                dialog = self.dialogs[0]
                if not dialog.armed():
                    return
                dialog.pick_number(int(digit))
                if dialog.text_mode:
                    self._focus_dialog()

        @kb.add("escape", filter=has_dialog)
        def _dialog_escape(event):
            dialog = self.dialogs[0]
            if dialog.text_mode and len(dialog.options) > 1:
                dialog.text_mode = False
                self._focus_dialog()
            else:
                dialog.resolve(None)

        @kb.add("backspace", filter=has_dialog, eager=True)
        def _dialog_backspace(event):
            dialog = self.dialogs[0]
            if dialog.text_mode or dialog.filterable:
                buffer = self.dialog_text if dialog.text_mode else self.dialog_filter
                self._delete_input(buffer, backward=True)

        @kb.add("delete", filter=has_dialog, eager=True)
        def _dialog_delete(event):
            dialog = self.dialogs[0]
            if dialog.text_mode or dialog.filterable:
                buffer = self.dialog_text if dialog.text_mode else self.dialog_filter
                self._delete_input(buffer, backward=False)

        # ---- input keys
        @kb.add("enter", filter=no_dialog)
        def _enter(event):
            buffer = self.input_buffer
            menu = self.menu
            # A suggestion that is already typed out in full changes nothing: Enter then sends the line.
            # Folders behave like a shell: Tab completes, Enter runs — unless a row was picked with the arrows.
            wanted = bool(menu and menu.items and (menu.kind != "path" or menu.index is not None))
            if wanted and menu.kind == "command" and menu.index is None and self.commands.exists(buffer.text):
                wanted = False                  # a whole command name was typed: run it, not the first look-alike
            if wanted and self._apply_suggestion(menu.current):
                text = buffer.text
                if text.startswith("/") and self.commands.runs_immediately(text.strip()):
                    self.submit()
                elif not text.endswith(" ") and menu.kind != "path" and not _keeps_going(menu.current.text):
                    buffer.insert_text(" ")
                return
            before = buffer.document.text_before_cursor
            if _continues_line(before):
                buffer.delete_before_cursor(1)
                buffer.insert_text("\n")
                return
            if self.search_query is not None:
                self._finish_search(accept=True)
                return
            self.submit()

        @kb.add("escape", "enter", filter=no_dialog)
        @kb.add("c-j", filter=no_dialog)
        def _newline(event):
            self.input_buffer.insert_text("\n")

        @kb.add("s-tab", filter=no_dialog)
        def _cycle_mode(event):
            self.runtime.permissions.cycle(source="keys")

        @kb.add("tab", filter=no_dialog)
        def _tab(event):
            buffer = self.input_buffer
            menu = self.menu
            if menu and menu.items:
                self._apply_suggestion(menu.current)
                if buffer.text.startswith("/") and " " not in buffer.text:
                    buffer.insert_text(" ")
            elif self._completion_context():
                self._refresh_menu(force=True)              # Tab brings back a list hidden with Esc
            else:
                buffer.insert_text("    ")

        @kb.add("backspace", filter=no_dialog & Condition(lambda: self.search_query is None), eager=True)
        def _backspace(event):
            self._delete_input(self.input_buffer, backward=True)

        @kb.add("delete", filter=no_dialog, eager=True)
        def _delete(event):
            self._delete_input(self.input_buffer, backward=False)

        @kb.add("up", filter=no_dialog)
        def _history_up(event):
            buffer = self.input_buffer
            if self.menu and self.menu.items:
                self.menu.move(-1)
                return
            if not buffer.text and self.queue:
                item = self.queue.pop()
                if item.get("attachments"):
                    self.attachments = item["attachments"]
                buffer.document = Document(str(item.get("display") or item.get("content") or ""))
                return
            if buffer.document.cursor_position_row == 0:
                previous = self.history.previous(buffer.text)
                if previous is not None:
                    buffer.document = Document(previous, len(previous))
            else:
                buffer.cursor_up()

        @kb.add("down", filter=no_dialog)
        def _history_down(event):
            buffer = self.input_buffer
            if self.menu and self.menu.items:
                self.menu.move(1)
                return
            if buffer.document.cursor_position_row >= buffer.document.line_count - 1:
                following = self.history.next()
                if following is not None:
                    buffer.document = Document(following, len(following))
            else:
                buffer.cursor_down()

        @kb.add("escape", filter=no_dialog)
        def _escape(event):
            now = time.monotonic()
            double = now - self.last_escape < 0.8
            self.last_escape = now
            buffer = self.input_buffer
            if self.search_query is not None:
                self._finish_search(accept=False)
                return
            if self.menu:
                self._close_menu()
                return
            if self.show_help:
                self.show_help = False
                return
            if self.busy:
                self.interrupt()
                return
            if double:
                if buffer.text:
                    buffer.reset()
                    self.attachments.clear()
                else:
                    self.run_command_text("/rewind")

        @kb.add("?", filter=no_dialog & empty)
        def _help(event):
            self.show_help = not self.show_help

        @kb.add("c-r", filter=no_dialog)
        def _search(event):
            if self.search_query is None:
                self.search_query = ""
                self.search_hit = None
            else:
                before = self.search_hit[0] if self.search_hit else None
                hit = self.history.search(self.search_query, before)
                if hit:
                    self.search_hit = hit

        @kb.add("backspace", filter=no_dialog & Condition(lambda: self.search_query is not None), eager=True)
        def _search_back(event):
            self.search_query = (self.search_query or "")[:-1]
            self.search_hit = self.history.search(self.search_query) if self.search_query else None

        @kb.add(Keys.Any, filter=no_dialog & Condition(lambda: self.search_query is not None), eager=True)
        def _search_type(event):
            self.search_query = (self.search_query or "") + event.data
            self.search_hit = self.history.search(self.search_query)

        @kb.add("c-g", filter=no_dialog)
        def _editor(event):
            asyncio.ensure_future(self.external_editor())

        @kb.add("c-v", filter=no_dialog)
        @kb.add("escape", "v", filter=no_dialog)
        @kb.add("escape", "м", filter=no_dialog)   # r33: the same physical key in the Russian layout
        def _paste_image(event):
            asyncio.ensure_future(self.paste_image(fallback_text=True))

        @kb.add("escape", "p", filter=no_dialog)
        @kb.add("escape", "з", filter=no_dialog)   # r33: the same physical key in the Russian layout
        def _model(event):
            self.run_command_text("/model")

        @kb.add("escape", "r", filter=no_dialog)
        @kb.add("escape", "к", filter=no_dialog)   # r33: the same physical key in the Russian layout
        def _thinking_view(event):
            self._toggle_thinking()

        @kb.add("escape", "t", filter=no_dialog)
        @kb.add("escape", "е", filter=no_dialog)   # r33: the same physical key in the Russian layout
        def _thinking(event):
            value = not bool(self.settings.get("thinking"))
            self.settings.overrides["thinking"] = value
            self.settings.reload()
            self.say(tr("Размышления: ", "Thinking: ") + (tr("вкл", "on") if value else tr("выкл", "off")))

        @kb.add(Keys.BracketedPaste, filter=no_dialog)
        def _paste(event):
            self.handle_paste(event.data)

        @kb.add("c-_", filter=no_dialog)
        @kb.add("c-z", filter=no_dialog)
        def _undo(event):
            self.input_buffer.undo()

        return kb

    # ------------------------------------------------------------ input actions
    def _edit_clicked_paste(self) -> None:
        pos = self.input_buffer.cursor_position
        for match in re.finditer(r"\[Pasted text #(\d+) \+\d+ lines\]", self.input_buffer.text):
            number = int(match.group(1))
            if match.start() <= pos <= match.end() and number in self.attachments.pastes:
                asyncio.create_task(self._edit_paste(number, match.group(0)))
                return

    async def _edit_paste(self, number: int, token: str) -> None:
        value = await self.prompt_text(f"Edit pasted text #{number}", default=self.attachments.pastes[number], tag="paste")
        if value is not None:
            self.attachments.pastes[number] = value
            updated = f"[Pasted text #{number} +{value.count(chr(10)) + 1} lines]"
            self.input_buffer.text = self.input_buffer.text.replace(token, updated, 1)

    def _finish_search(self, *, accept: bool) -> None:
        if accept and self.search_hit:
            text = self.search_hit[1]
            self.input_buffer.document = Document(text, len(text))
        self.search_query = None
        self.search_hit = None

    @staticmethod
    def _delete_input(buffer: Buffer, *, backward: bool) -> None:
        """Delete selections or whole attachment chips, silently at boundaries."""
        if buffer.selection_state:
            buffer.cut_selection()
            return
        pos = buffer.cursor_position
        for match in re.finditer(r"\[Image #\d+\]|\[File #\d+: [^\]]+\]|\[Pasted text #\d+ \+\d+ lines\]", buffer.text):
            if (backward and match.start() < pos <= match.end()) or (not backward and match.start() <= pos < match.end()):
                buffer.document = Document(buffer.text[:match.start()] + buffer.text[match.end():], match.start())
                return
        if backward and pos:
            buffer.delete_before_cursor(1)
        elif not backward and pos < len(buffer.text):
            buffer.delete(1)

    PASTE_CHARS = 800          # a paste longer than this becomes a "[Pasted text #n +m lines]" chip
    PASTE_LINES = 10

    def handle_paste(self, data: str) -> None:
        data = data.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
        if not data:
            return
        buffer = self.input_buffer
        if buffer.selection_state:
            buffer.cut_selection()                      # a paste replaces what is selected
        found = pasted_paths(data)
        if found:
            for path in found:
                self.attach_file(path, pasted=True)
            return
        if len(data) > self.PASTE_CHARS or data.count("\n") + 1 >= self.PASTE_LINES:
            buffer.insert_text(self.attachments.paste_token(data))
            return
        buffer.insert_text(data)

    def attach_file(self, name: str, *, pasted: bool = False) -> bool:
        """Attach a picture or a small text file as a chip.

        A pasted or dropped path that cannot be a chip — a folder, a big or a binary file — is not
        an error: the path itself goes into the message, and the agent opens it with its tools.
        """
        raw = name.strip().strip('"').strip("'")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = self.runtime.root / path
        if not path.exists():
            self.say(f"File not found: {path}", "warn")
            return False
        token, problem = self._chip_for(path)
        if not token:
            if not pasted:
                self.say(f"Could not attach {path.name}: {problem}", "warn")
                return False
            token = f'"{raw}"' if " " in raw else raw
        self.input_buffer.insert_text(token + " ")
        return True

    def _chip_for(self, path: Path) -> tuple:
        """(chip, "") for a picture or a small text file, else ("", why not)."""
        image = image_from_path(str(path))
        if image:
            return self.attachments.image_token(image), ""
        try:
            return self.attachments.file_token(path), ""
        except (OSError, ValueError) as exc:
            return "", str(exc)

    async def paste_image(self, *, fallback_text: bool = False) -> None:
        """Alt+V / Ctrl+V / click: a picture or copied files from the clipboard, else (Ctrl+V) its text."""
        from .. import clipboard
        want_image = clipboard.has_image() or clipboard.has_files() or not clipboard.sequence_number()
        image = await asyncio.to_thread(clipboard_image) if want_image else None
        if image:
            self.input_buffer.insert_text(self.attachments.image_token(image) + " ")
            self.clipboard_image_seen = False
            return
        if fallback_text:
            try:
                text = await asyncio.to_thread(clipboard.read_text)
            except Exception:
                text = ""
            if text:
                self.handle_paste(text)
                return
        self.say("No image in the clipboard", "warn")

    async def external_editor(self) -> None:
        """Ctrl+G: edit the message in a roomier field. With an editor of your own chosen (``editor`` setting,
        ``$VISUAL``, ``$EDITOR``) that editor is used — a window editor leaves this screen as it is."""
        from ..editor import configured_editor, open_external
        command = configured_editor(self.settings)
        if command:
            handle, name = tempfile.mkstemp(suffix=".md", prefix="memucode-")
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(self.input_buffer.text)
            try:
                if await open_external(self, Path(name), command):
                    text = Path(name).read_text(encoding="utf-8").rstrip("\n")
                    self.input_buffer.document = Document(text, len(text))
                    return
            finally:
                try:
                    os.unlink(name)
                except OSError:
                    pass
        value = await self.prompt_text(tr("Сообщение", "Message"), default=self.input_buffer.text, tag="paste")
        if value is not None:
            value = value.rstrip("\n")
            self.input_buffer.document = Document(value, len(value))

    def submit(self) -> None:
        buffer = self.input_buffer
        raw = buffer.text
        if not raw.strip():
            return
        # Some Windows terminals deliver dropped paths as keystrokes instead
        # of a bracketed paste. Convert the complete path before dispatch.
        dropped = pasted_paths(raw)
        if dropped and all(Path(item).is_file() for item in dropped):
            chips = [self._chip_for(Path(item))[0] for item in dropped]
            if all(chips):
                buffer.document = Document(" ".join(chips) + " ")
                return
            # Not all of them can be chips (a big or a binary file): the line is an ordinary message.
        self.history.add(raw)
        buffer.reset()
        # Round 33: Buffer.reset() does not fire on_text_changed — an argument list («status», «auto») stayed on
        # screen under an empty input after «/goal status» or «/auto-continue auto» was sent.
        self._refresh_menu()
        self.show_help = False
        self._auto_resumes = 0
        text = raw.strip("\n")
        if self.busy:
            if self._while_busy(text):
                return
            content, images = self.attachments.expand(text)
            # "display" keeps the chips (↑ puts it back into the input box), "shown" is what the chat prints.
            self.queue.append({"content": self._content(content, images), "display": text,
                               "shown": self.attachments.expand_pastes(text), "attachments": self.attachments})
            self.attachments = Attachments(counter=self.attachments.counter)
            self.invalidate()
            return
        self.dispatch(text)

    def _while_busy(self, text: str) -> bool:
        """The agent is working and the user typed ``text``. True when it was handled here.

        A command is never a message for the model. One that cannot disturb the work (a list, a setting, a
        dialog) runs at once; one that would (it starts a turn, switches the conversation or the folder) is
        queued as a *command* and runs when the agent is done. ``!command`` is the user's own shell: it waits
        too, because its record must not land between a tool call and its result."""
        if text.startswith("#") and not text.startswith("##") and len(text) > 1:
            self.start(self.save_memory(text[1:].strip()), busy=False)
            return True
        if text.startswith("&") and len(text) > 1:
            self.run_command_text("/background " + text[1:].strip())
            return True
        if text.startswith("!") and len(text) > 1:
            self._queue_command(text, text)
            return True
        if not _is_command(text):
            return False
        if self.commands.allowed_while_busy(text) or not self.commands.exists(self.commands.split(text)[0]):
            self.run_command_text(text)             # runs now (an unknown name just says so)
            return True
        self._queue_command(text, text)
        return True

    def _queue_command(self, text: str, display: str) -> None:
        self.queue.append({"command": text, "display": display, "content": display})
        name = text.split(None, 1)[0]
        self.say(tr(f"{name} выполнится, когда агент закончит", f"{name} will run when the agent is done"))
        self.invalidate()

    def dispatch(self, text: str) -> None:
        if text.startswith("!") and len(text) > 1:
            self.start(self.run_bash(text[1:].strip()))
            return
        if text.startswith("#") and not text.startswith("##") and len(text) > 1:
            self.start(self.save_memory(text[1:].strip()))
            return
        if text.startswith("&") and len(text) > 1:
            self.run_command_text("/background " + text[1:].strip())
            return
        if _is_command(text):
            self.run_command_text(text)
            return
        self.send_prompt(text)

    def run_command_text(self, text: str) -> None:
        self.start(self.commands.execute(text), busy=not self.commands.allowed_while_busy(text))

    def _content(self, text: str, images: List[Dict[str, Any]]) -> Any:
        text = self._inline_mentions(text)
        if not images:
            return text
        return [{"type": "text", "text": text}, *[{"type": "image", **image} for image in images]]

    def _inline_mentions(self, text: str) -> str:
        """Attach the files the prompt mentions with @path (like Claude Code)."""
        from ..tools.base import display_path, workspace_of
        from ..tools.files import remember_read
        blocks: List[str] = []
        for match in re.finditer(r"(?<![\w@])@(\"[^\"]+\"|[^\s@]+)", text):
            raw = match.group(1).strip('"').rstrip(".,;:!?)")
            if raw.startswith("agent-"):
                continue
            path = Path(raw) if Path(raw).is_absolute() else (self.runtime.root / raw)
            try:
                path = path.resolve()
            except OSError:
                continue
            if workspace_of(self.runtime, path) is None or not path.exists():
                continue
            if path.is_dir():
                entries = sorted(item.name + ("/" if item.is_dir() else "") for item in path.iterdir())[:200]
                blocks.append(f'<folder path="{display_path(self.runtime, path)}">\n' + "\n".join(entries) + "\n</folder>")
                continue
            try:
                if path.stat().st_size > 400_000 or path.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".exe", ".dll", ".zip"):
                    continue
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lines = content.splitlines()
            numbered = "\n".join(f"{index + 1:>5}\t{line}" for index, line in enumerate(lines[:2000]))
            more = f"\n… [{len(lines) - 2000} more lines: use Read]" if len(lines) > 2000 else ""
            blocks.append(f'<file path="{display_path(self.runtime, path)}">\n{numbered}{more}\n</file>')

            class _Ctx:
                session = self.runtime.session
            remember_read(_Ctx(), path)
        if not blocks:
            return text
        return text + "\n\n<attached_files>\n" + "\n".join(blocks) + "\n</attached_files>"

    async def prompt_inline(self, text: str, *, display: str, kind: str = "command") -> None:
        """Run a prompt from inside a command that is already the busy task."""
        self.show_user(display, kind="command")
        await self.run_agent(self._content(text, []), display=display, kind=kind)

    def send_prompt(self, text: str, *, display: Optional[str] = None, kind: str = "prompt") -> None:
        expanded, images = self.attachments.expand(text)
        shown = display if display is not None else self.attachments.expand_pastes(text)
        self.attachments.clear()
        self.show_user(shown, images=len(images), kind="command" if kind == "command" else "prompt")
        content = self._content(expanded, images)
        if self.runtime.session_context:
            extra = "\n\n<session_start_context>\n" + "\n".join(self.runtime.session_context) + "\n</session_start_context>"
            self.runtime.session_context = []
            content = content + extra if isinstance(content, str) else [*content, {"type": "text", "text": extra}]
        # The stored "display" is what a resumed session prints: needed whenever the model received
        # more than the person wrote (pictures, attached file contents, the session context).
        same = isinstance(content, str) and content == shown
        self.start(self.run_agent(content, display="" if same and display is None else shown, kind=kind))

    def start(self, coroutine, *, busy: bool = True) -> None:
        # The interface is busy from this very moment, not from when the task first runs: a second Enter in
        # between must queue instead of starting a second agent on the same conversation.
        if busy:
            self._set_busy(True)

        async def wrapper():
            try:
                await coroutine
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                import traceback
                self.runtime.log_error(traceback.format_exc())
                self.write(self.renderer.error("Internal error", f"{type(exc).__name__}: {exc}"))
            finally:
                if busy:
                    self._set_busy(False)
                    self._drain_queue()

        task = asyncio.ensure_future(wrapper())
        if busy:
            self.task = task
        else:
            self.side_tasks.append(task)
            task.add_done_callback(lambda t: self.side_tasks.remove(t) if t in self.side_tasks else None)

    def _set_busy(self, value: bool) -> None:
        self.busy = value
        if value:
            self.busy_started = time.monotonic()
            self.verb = random.choice(VERBS)
            self.turn_out_tokens = 0.0
            self.tip = random.choice(TIPS)
            _set_title("● MemuCode — " + (self.runtime.session.title() or self.runtime.root.name))
        else:
            self.status_text = ""
            self.active_tools.clear()
            _set_title("MemuCode — " + (self.runtime.session.title() or self.runtime.root.name))
        self.invalidate()

    def _drain_queue(self) -> None:
        while self.queue and not self.busy:
            item = self.queue.pop(0)
            if item.get("command"):
                # A queued command runs as a command, never as a prompt. A command that only shows or sets
                # something leaves the interface free, so the next queued item may follow at once.
                self.dispatch(str(item["command"]))
                continue
            text = str(item.get("shown") or item.get("display") or "")
            self.show_user(text)
            self.start(self.run_agent(item["content"], display=text))

    def interrupt(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
        self.status_text = "interrupting…"
        self.invalidate()

    # ------------------------------------------------------------ work
    async def run_agent(self, content: Any, *, display: str = "", kind: str = "prompt") -> None:
        from ..agent import Agent
        runtime = self.runtime
        if self.connecting:
            # The models load in the background; a message sent right away simply waits for them.
            self.status_text = "connecting…"
            await runtime.wait_ready(40)
            self.status_text = ""
        if not runtime.model:
            self.say("No model selected. Pick one with /model.", "warn")
            return
        routed = None
        text = request_text(content)
        looked = await self._look_at(kind, text)
        if kind == "prompt" and (runtime.settings.get("model_routing") or {}).get("enabled"):
            try:
                routed = await runtime.route_model(text, looked_at=looked if looked is not None else False)
            except Exception:
                routed = None
        started = time.monotonic()
        try:
            if looked is not None:
                try:
                    await self._act_on(looked)
                except Exception:                    # a hint about the request must never stop the request
                    runtime.log_error(traceback.format_exc())
            agent = Agent(runtime, model=routed[0] if routed else "")
            if routed and routed[0] != runtime.model:
                self.say(f"→ {routed[0]} ({routed[1]})")
            self.current_agent = agent
            result = await agent.run_turn(content, kind=kind, display=display)
            runtime.stats.turns += 1
            while True:
                follow = await runtime.goal_followup(result)
                if not follow:
                    break
                goal = runtime.session.meta.get("goal") or {}
                self.say(f"◎ Goal: continuing ({goal.get('continuations')}/{goal.get('max', 10)})")
                result = await agent.run_turn(follow, kind="command", display="↻ goal")
        finally:
            runtime.turn_hints = []
        elapsed = time.monotonic() - started
        if elapsed > 30 and result.stopped == "done":
            minutes = f"{int(elapsed // 60)}m {int(elapsed % 60)}s" if elapsed >= 60 else f"{int(elapsed)}s"
            self.add_block(lambda width, expanded: self.renderer.render(Text(f"✻ Worked for {minutes}", style="subtle"),
                                                                        width=width), kind="notice")
        if elapsed > float(self.settings.get("notify_after_seconds") or 25):
            self._notify("Done", (result.text or result.error or "")[:160])
        if self.settings.get("auto_title", True) and not runtime.session.meta.get("title") and result.stopped == "done":
            asyncio.ensure_future(self._make_title())

    # ------------------------------------------------------------ scheduled tasks
    def _check_schedule(self) -> None:
        """Start the tasks of ``/schedule`` that are due. A run that was missed because no window was open is not
        made up for here (that is what the Windows scheduler is for): it is only marked and moved on."""
        try:
            from .. import schedule
            now = time.time()
            tasks = [task for task in schedule.load_tasks() if task.id not in self._scheduled_running]
            for task in schedule.due(tasks, now):
                if task.next_run < now - 300:
                    schedule.update_task(task.id, last_status="missed")
                    schedule.advance(task, now)
                    continue
                self.run_scheduled(task)
        except Exception:
            self.runtime.log_error(traceback.format_exc())

    def run_scheduled(self, task: Any, *, manual: bool = False) -> None:
        """Run a ``/schedule`` task in a process of its own, so that this window stays free for work."""
        if task.id in self._scheduled_running:
            self.say(tr(f"«{task.title}» уже выполняется", f"“{task.title}” is already running"), "warn")
            return
        self._scheduled_running.add(task.id)
        self.say(tr(f"◷ Запускаю задачу «{task.title}»", f"◷ Starting the task “{task.title}”"))
        self.start(self._scheduled_job(task), busy=False)

    async def _scheduled_job(self, task: Any) -> None:
        from .. import schedule
        started = time.time()
        failure = ""
        try:
            process = await asyncio.create_subprocess_exec(
                *schedule.command_line("schedule", "run", task.id), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            _out, err = await process.communicate()
            if process.returncode not in (0, 1):               # 1 is "the task failed", which the task itself records
                failure = (err.decode("utf-8", "replace").strip().splitlines() or [f"exit code {process.returncode}"])[-1][:200]
        except Exception as exc:
            failure = str(exc)
        finally:
            self._scheduled_running.discard(task.id)
        fresh = schedule.get_task(task.id)
        if fresh is not None and fresh.running_since and failure:
            schedule.update_task(fresh.id, running_since=0.0, last_status="error", last_summary=failure[:300], last_run=started)
            fresh = schedule.get_task(task.id)
        if failure:
            self.say(tr(f"Задача «{task.title}» не отработала: {failure}", f"The task “{task.title}” did not run: {failure}"), "error")
            return
        if fresh is not None:
            level = "success" if fresh.last_status == "ok" else "warn"
            self.say(f"◷ «{fresh.title}»: {fresh.last_status or '?'} — {fresh.last_summary[:160]}  (/schedule log {fresh.id})", level)

    # ------------------------------------------------------------ a look at the request
    def _may_offer_plan(self) -> bool:
        """Plan mode is offered only where it helps (the modes that ask or accept edits, not auto/bypass/plan
        itself) and not on every message: once in five turns at most."""
        runtime = self.runtime
        return (runtime.permissions.mode in ("default", "acceptEdits")
                and runtime.stats.turns - getattr(self, "_plan_offered_turn", -99) >= 5)

    async def _look_at(self, kind: str, text: str) -> Any:
        """What the decision model makes of the request — how hard it is (for ``/model auto``), which skill fits,
        whether a plan should come first. Short, and it never fails the work: None means "no opinion"."""
        runtime = self.runtime
        decisions = runtime.decisions
        if kind != "prompt" or not worth_looking_at(text) or not decisions.enabled or decisions.provider == "model":
            return None
        routing = bool((runtime.settings.get("model_routing") or {}).get("enabled"))
        self.status_text = tr("смотрю на запрос", "looking at the request")
        try:
            return await runtime.triage(text, routing=routing, skills=True, intent=self._may_offer_plan())
        except Exception:
            return None
        finally:
            self.status_text = ""

    async def _act_on(self, looked: Any) -> None:
        """Use what the look at the request found: tell the model about a skill that fits (and the person), and
        offer plan mode for a big task."""
        runtime = self.runtime
        runtime.turn_hints = []
        if looked.skill:
            skill = str(looked.skill["skill"])
            chance = float(looked.skill.get("probability") or 0.0)
            runtime.turn_hints.append(
                f'The decision model thinks the skill "{skill}" fits this request ({chance:.0%}). If it does, load it '
                f"with the Skill tool before you start; if it does not, ignore this note.")
            self.say(tr(f"◆ Похоже, подойдёт скилл «{skill}»", f"◆ The skill “{skill}” looks relevant"))
        if looked.wants_plan and self._may_offer_plan():
            await self._offer_plan()

    async def _offer_plan(self) -> None:
        runtime = self.runtime
        self._plan_offered_turn = runtime.stats.turns
        dialog = dlg.Dialog(
            title=tr("Это большая задача — начать с плана?", "This looks like a big task — plan it first?"),
            options=[
                dlg.Option(tr("Да, сначала план", "Yes, plan first"), "plan",
                           tr("MemuCode исследует код и покажет план на одобрение; ничего не меняет",
                              "MemuCode researches the code and shows a plan for your approval; nothing changes")),
                dlg.Option(tr("Нет, сразу за работу", "No, just do it"), "go"),
                dlg.Option(tr("Больше не предлагать", "Don't suggest this again"), "never",
                           tr("вернуть можно в /decisions → «Для чего использовать»",
                              "you can turn it back on in /decisions → “What to use it for”")),
            ], guard=True)
        choice = await self.open_dialog(dialog)
        if choice == "plan":
            runtime.permissions.set_mode("plan", source="plan")
        elif choice == "never":
            runtime.settings.set("decisions.uses.intent", False)
            self.say(tr("Хорошо, режим плана больше не предлагается.", "Okay, plan mode will not be suggested again."))

    async def _make_title(self) -> None:
        session = self.runtime.session
        prompt = session.last_user_text()[:2000]
        if not prompt:
            return
        try:
            title = await asyncio.wait_for(self.runtime.quick_answer(
                "Write a short title (3-7 words, no quotes, no trailing punctuation) for a coding session that "
                "starts with the user's request below. Use the language of the request.", prompt), 30)
        except Exception:
            return
        title = title.strip().strip('"').splitlines()[0][:80] if title.strip() else ""
        if title:
            session.set_meta(title=title)
            session.flush_index()
            _set_title("MemuCode — " + title)

    async def run_bash(self, command: str) -> None:
        from ..tools.base import ToolCall
        from ..tools.shell import run_shell
        runtime = self.runtime
        self.show_user(command, kind="bash")
        call = ToolCall(name="Bash", args={"command": command}, display=command)
        call.started = time.monotonic()
        self.active_tools[call.id] = call
        try:
            cwd = Path(runtime.session.cwd or runtime.root)
            result = await run_shell(runtime, command, cwd=cwd, timeout=600,
                                     on_output=lambda text: self.tool_progress(None, call, text))
        except Exception as exc:
            self.active_tools.pop(call.id, None)
            self.write(self.renderer.error("The command did not start", str(exc)))
            return
        self.active_tools.pop(call.id, None)
        output = result["output"].rstrip()
        from ..tools.base import ToolResult
        code = result["exit_code"]
        summary = f"exit {code} · {result['ms'] / 1000:.1f}s"
        block = ToolResult(ok=code == 0, output=output, summary=summary, detail=output, error="" if code == 0 else summary)
        call.status = "done" if code == 0 else "error"
        self.add_block(lambda width, expanded: self.renderer.tool_block(call, block, verbose=True, width=width),
                       kind="notice")
        runtime.session.append({"role": "user", "kind": "bash", "display": "!" + command, "content": (
            f"<bash-input>{command}</bash-input>\n<bash-stdout>{output[-12000:]}</bash-stdout>\n"
            f"<bash-exit-code>{code}</bash-exit-code>\n(The user ran this command in the terminal themselves; "
            "take its output into account in the next answer.)")})

    async def save_memory(self, text: str) -> None:
        from ..project import add_memory
        choice = await self.select(tr("Куда записать заметку?", "Where should this memory go?"), [
            dlg.Option(tr("Проект: MEMUCODE.md", "Project: MEMUCODE.md"), "project",
                       tr("общие правила проекта (попадают в git)", "shared project rules (checked into git)")),
            dlg.Option(tr("Проект, лично: MEMUCODE.local.md", "Project, personal: MEMUCODE.local.md"), "local",
                       tr("только для вас, в .gitignore", "just for you, git-ignored")),
            dlg.Option(tr("Мои правила для всех проектов", "My rules for every project"), "user",
                       str(paths.user_memory_file())),
        ], body=self.renderer.render(Text("  # " + text, style="memory")))
        if not choice:
            return
        target = add_memory(self.runtime.root, text, choice)
        self.runtime.invalidate_prompt(memory=True)
        self.say(tr(f"Записано в {target}", f"Saved to {target}"), "success")

    # ------------------------------------------------------------ lifecycle
    def exit(self, code: int = 0) -> None:
        self.exit_code = code
        if self.app is not None and self.app.is_running:
            self.app.exit()

    def files_ready(self) -> None:
        """The project index finished a scan (called from its own thread): a ``@`` list that was
        waiting for it is rebuilt."""
        loop = getattr(self, "_loop", None)
        if loop is None or self.app is None or not self.app.is_running:
            return

        def refresh() -> None:
            if self.menu is None or self.menu.kind == "mention":
                if self.completer.kind(self.input_buffer.document.text_before_cursor) == "mention":
                    self._refresh_menu(force=True)
            self.invalidate()

        try:
            loop.call_soon_threadsafe(refresh)
        except RuntimeError:
            pass

    async def run(self, *, initial: Optional[str] = None, after_start=None, input: Any = None,
                  output: Any = None) -> int:
        self._loop = asyncio.get_running_loop()
        # The keyboard is read on a thread of the loop's default pool (prompt_toolkit, Windows). The stock pool has
        # a handful of threads, and a handful of slow reads (a search, a hook, a big file) would leave the keys
        # unread — Esc included — so the pool is made roomy. Threads are only started when there is work.
        from concurrent.futures import ThreadPoolExecutor
        self._loop.set_default_executor(ThreadPoolExecutor(max_workers=64, thread_name_prefix="memucode-io"))
        self.app = self.make_app(input=input, output=output)
        ticker_task = asyncio.ensure_future(self._ticker())

        async def boot() -> None:
            await asyncio.sleep(0.05)
            if after_start is not None:
                await after_start(self)
            if initial:
                self.input_buffer.text = initial
                self.submit()

        boot_task = asyncio.ensure_future(boot())
        threading.Thread(target=self._warm_up, daemon=True).start()
        _set_title("MemuCode — " + self.runtime.root.name)
        fine_timer = _fine_timer(True)
        try:
            from .. import splash
            splash.clear()
            await self.app.run_async()
        finally:
            if fine_timer:
                _fine_timer(False)
            for task in (boot_task, ticker_task, *self.side_tasks):
                task.cancel()
            if self.task and not self.task.done():
                self.task.cancel()
                try:
                    await asyncio.wait_for(self.task, 5)
                except BaseException:
                    pass
        return self.exit_code

    def _warm_up(self) -> None:
        """Work for after the first frame: imports and caches the first message would otherwise wait for."""
        try:
            # Round 21: httpx больше не импортируется до первого кадра (см.
            # llm.LazyModule) — он нужен фоновой загрузке моделей сразу после.
            import httpx  # noqa: F401
            from .render import warm_up
            warm_up()
            self.runtime.prewarm()
            from ..llm import shared_ssl_context
            if any(str(row.get("base_url") or "").startswith("https") for row in self.runtime.settings.connections()):
                shared_ssl_context()
        except Exception:
            pass

    async def _ticker(self) -> None:
        """Animation frames while something is going on, and the clipboard hint."""
        from .. import clipboard
        last_clip = clipboard.sequence_number()
        while True:
            background = bool(self.runtime.jobs.running() or self.runtime.shells.running())
            animating = bool(self.busy or self.active_tools or self.connecting or background)
            await asyncio.sleep(0.11 if animating else 0.4)
            now = time.monotonic()
            changed = animating or self.copied_until > now or (self.exit_armed and now - self.exit_armed < 2)
            if self.runtime.permissions.sync_from_disk() is not None:     # another window chose a mode
                changed = True
            if now - self._schedule_checked > 20:
                self._schedule_checked = now
                self._check_schedule()
            try:
                if self.auto_continue.tick():                # Round 33: /auto-continue countdown and send
                    changed = True
            except Exception:
                self.runtime.log_error(traceback.format_exc())
            for line in self.runtime.poll_background():
                self.say(line, "info")
                changed = True
            if self.runtime.notes:
                self.maybe_wake()
            sequence = clipboard.sequence_number()
            if sequence != last_clip:
                last_clip = sequence
                seen = clipboard.has_image()
                if seen != self.clipboard_image_seen:
                    self.clipboard_image_seen = seen
                    changed = True
            if changed:
                self.invalidate()


# ---------------------------------------------------------------- helpers

_WORKERS: Any = None


def _suggestion_workers() -> Any:
    """The threads that look for suggestions (files, folders). They are their own small pool, not the loop's default
    one: prompt_toolkit waits for the keyboard on a thread of the default pool, and a few slow disk searches that
    filled it left the keys unread for seconds."""
    global _WORKERS
    if _WORKERS is None:
        from concurrent.futures import ThreadPoolExecutor
        _WORKERS = ThreadPoolExecutor(max_workers=2, thread_name_prefix="memucode-suggest")
    return _WORKERS


def _plain_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    return ""


def _keeps_going(text: str) -> bool:
    """A folder suggestion (``@src/``, ``@"my dir/``): the user goes on typing inside it, so no space follows."""
    return text.startswith("@") and (text.endswith("/") or (text.startswith('@"') and not text.endswith('"')))


def _is_command(text: str) -> bool:
    """``/name …`` is a slash command; ``//text`` and a line that starts with an existing absolute path
    (``/home/me/big.txt`` typed or dropped on macOS/Linux) are messages for the model."""
    if not text.startswith("/") or text.startswith("//"):
        return False
    first = text.split(None, 1)[0]
    if "/" not in first[1:]:
        return True
    try:
        return not Path(first).exists()
    except OSError:
        return True


_PATH_TAIL = re.compile(r'(?:^|\s)"?(?:[A-Za-z]:\\|\\\\|\.{1,2}\\|~\\)(?:[^\n]*\\)?$|[^\s\\]\\[^\s\\]+\\$')


def _continues_line(before: str) -> bool:
    """A trailing backslash + Enter starts a new line — unless the backslash ends a Windows path.

    ``/cd C:\\Users\\me\\`` or a prompt ending in ``src\\app\\`` is a path, and Enter must send it;
    slash commands never continue onto a second line.
    """
    if not before.endswith("\\") or before.endswith("\\\\"):
        return False
    last_line = before.rsplit("\n", 1)[-1]
    if before.lstrip().startswith("/") and "\n" not in before:
        return False
    return not _PATH_TAIL.search(last_line)


def _indent(text: str, prefix: str = "  ") -> str:
    return "\n".join(prefix + line for line in str(text).splitlines())


def _fine_timer(on: bool) -> bool:
    """Ask Windows for 1 ms timers while the interface runs.

    By default a timer on Windows fires on a 15.6 ms grid, so every redraw that prompt_toolkit postponed
    by a few milliseconds (two changes close together: a key, then its result) was drawn 16 ms late.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        winmm = ctypes.windll.winmm
        return (winmm.timeBeginPeriod(1) if on else winmm.timeEndPeriod(1)) == 0
    except Exception:
        return False


def _set_title(title: str) -> None:
    try:
        if os.name == "nt":
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW(str(title))
            return
        sys.stdout.write(f"\x1b]0;{title}\x07")
        sys.stdout.flush()
    except Exception:
        pass


_BRANCH_CACHE: Dict[str, tuple] = {}


def _git_branch(root: Path) -> str:
    key = str(root)
    cached = _BRANCH_CACHE.get(key)
    if cached and time.monotonic() - cached[1] < 5:
        return cached[0]
    branch = ""
    head = root / ".git" / "HEAD"
    try:
        if head.is_file():
            text = head.read_text(encoding="utf-8").strip()
            branch = text.split("refs/heads/", 1)[1] if "refs/heads/" in text else text[:8]
        elif (root / ".git").is_file():
            pointer = (root / ".git").read_text(encoding="utf-8").strip()
            if pointer.startswith("gitdir:"):
                real = Path(pointer[7:].strip())
                real = real if real.is_absolute() else root / real
                text = (real / "HEAD").read_text(encoding="utf-8").strip()
                branch = text.split("refs/heads/", 1)[1] if "refs/heads/" in text else text[:8]
    except OSError:
        branch = ""
    _BRANCH_CACHE[key] = (branch, time.monotonic())
    return branch


def _clipboard_text() -> str:
    if os.name == "nt":
        completed = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                                   capture_output=True, timeout=5, creationflags=0x08000000)
        return completed.stdout.decode("utf-8", "replace").rstrip("\r\n")
    for command in (["pbpaste"], ["wl-paste"], ["xclip", "-o", "-selection", "clipboard"]):
        if shutil.which(command[0]):
            return subprocess.run(command, capture_output=True, timeout=5).stdout.decode("utf-8", "replace")
    return ""


def _frag_width(fragments: StyleAndTextTuples) -> int:
    from prompt_toolkit.utils import get_cwidth
    return sum(get_cwidth(part[1]) for part in fragments)


def _cut_fragments(fragments: StyleAndTextTuples, width: int, *, keep_end: bool = False) -> StyleAndTextTuples:
    """The fragments cut to ``width`` cells, styles and click handlers kept, with "…" where text was lost."""
    from prompt_toolkit.utils import get_cwidth
    if width <= 0:
        return []
    if _frag_width(fragments) <= width:
        return list(fragments)
    room = width - 1
    out: StyleAndTextTuples = []
    parts = list(reversed(fragments)) if keep_end else list(fragments)
    for part in parts:
        text = part[1]
        chars = list(reversed(text)) if keep_end else list(text)
        kept = []
        for char in chars:
            cells = get_cwidth(char)
            if cells > room:
                break
            kept.append(char)
            room -= cells
        piece = "".join(reversed(kept)) if keep_end else "".join(kept)
        if piece:
            out.append((part[0], piece, *part[2:]))
        if len(kept) < len(chars):
            break
    if keep_end:
        out.reverse()
        style = out[0][0] if out else (fragments[0][0] if fragments else "")
        return [(style, "…")] + out
    style = out[-1][0] if out else (fragments[0][0] if fragments else "")
    return out + [(style, "…")]


def fit_footer(left: StyleAndTextTuples, right: StyleAndTextTuples, width: int) -> StyleAndTextTuples:
    """One status line from the left and right parts that never lets them run into each other.

    The two parts used to be two windows of half the width each, so a long left part (the mode, the
    hints, "● 2 agents running · /tasks") was clipped mid-word and glued to the model name. Here the
    optional pieces go first, in order of how little they are missed: the keyboard hints, the git
    branch, the metric, the context share; then the agents label gets shorter, then the model name,
    and only then is the left text cut with "…". At least two spaces always separate the parts."""
    width = max(1, int(width) - 1)  # the last column of a terminal row is avoided: some wrap there
    gap = 2
    left, right = list(left), list(right)

    def over() -> bool:
        return _frag_width(left) + _frag_width(right) + (gap if left and right else 0) > width

    def drop_right(test) -> None:
        for index in range(len(right) - 1, 0, -1):  # the first fragment (the model) always stays
            if over() and test(right[index][1]):
                del right[index]

    # 1. keyboard hints on the mode line
    if over() and left and str(left[0][0]).startswith("class:mode."):
        hints = [i for i, part in enumerate(left) if i and part[0] == "class:subtle"]
        for index in reversed(hints):
            if over():
                del left[index]
    # 2..4. optional right pieces
    drop_right(lambda text: "⎇" in text)
    if over() and len(right) > 1 and not right[-1][1].lstrip().startswith("·  ctx"):
        if "⎇" not in right[-1][1]:
            del right[-1]  # the metric is the last piece
    drop_right(lambda text: "ctx" in text)
    # 5. the agents/commands label gets shorter
    for i, part in enumerate(left):
        if over() and "●" in part[1]:
            for short in (part[1].replace(" running · /tasks", " · /tasks"), part[1].replace(" running · /tasks", "")):
                if over():
                    left[i] = (part[0], short, *part[2:])
    # 6. the model name (its end is the informative part), not below 12 cells; on a very narrow
    #    terminal it goes entirely: the left part says what is going on and what the keys do now
    if over() and right:
        room = width - _frag_width(left) - gap
        right = _cut_fragments(right, room, keep_end=True) if room >= 12 else []
    # 7. the left text itself
    if over():
        left = _cut_fragments(left, width)
    pad = width - _frag_width(left) - _frag_width(right)
    return left + ([("", " " * max(0, pad))] if right else []) + right
