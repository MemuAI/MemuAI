"""Everything MemuCode prints, rendered with ``rich`` into ANSI strings.

Each finished block is rendered once, at the current width, into the TUI's
scrollable transcript. The live area (streaming tail, spinner, input) is drawn
by prompt_toolkit from the same renderer.
"""
from __future__ import annotations

import io
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from rich import box
from rich.console import Console, Group, RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..i18n import tr
from .theme import Palette, gradient

BULLET = "●"
ELBOW = "⎿"

_MARKDOWN: List[Any] = []


def markdown_class() -> Any:
    """rich's Markdown with left-aligned headings and unframed code — built on first use, because
    markdown-it and pygments cost ~60 ms to import and the first frame does not need them."""
    if not _MARKDOWN:
        from rich.markdown import CodeBlock, Heading, Markdown
        from rich.syntax import Syntax

        class _Heading(Heading):
            LEVEL_ALIGN = {tag: "left" for tag in ("h1", "h2", "h3", "h4", "h5", "h6")}

        class _CodeBlock(CodeBlock):
            def __rich_console__(self, console, options):
                code = str(self.text).rstrip()
                yield Syntax(code, self.lexer_name, theme=self.theme, word_wrap=True, padding=(0, 1),
                             background_color="default")

        class _Markdown(Markdown):
            elements = {**Markdown.elements, "heading_open": _Heading, "fence": _CodeBlock, "code_block": _CodeBlock}

        _MARKDOWN.append(_Markdown)
    return _MARKDOWN[0]


def warm_up() -> None:
    """Import what the first answer needs while the user is still reading the banner."""
    try:
        markdown_class()
        from pygments.lexers import get_lexer_by_name
        for name in ("python", "javascript", "json", "bash", "markdown"):
            get_lexer_by_name(name)
    except Exception:
        pass


TOOL_TITLES = {
    "Edit": "Update", "MultiEdit": "Update", "Grep": "Search", "Glob": "Find", "LS": "List",
    "TodoWrite": "Update Todos", "WebSearch": "Web Search", "WebFetch": "Fetch", "AskUserQuestion": "Ask",
    "ExitPlanMode": "Plan", "BashOutput": "Bash Output", "KillShell": "Kill Shell",
}


def tool_title(name: str) -> str:
    if name.startswith("mcp__"):
        parts = name.split("__")
        return f"{parts[1]} · {'__'.join(parts[2:])}" if len(parts) > 2 else name
    return TOOL_TITLES.get(name, name)


class Renderer:
    def __init__(self, palette: Palette, color_system: str = "truecolor") -> None:
        self.palette = palette
        self.color_system = None if color_system in (None, "none") else ("256" if color_system == "256" else "truecolor")
        from .theme import rich_theme
        self.theme = rich_theme(palette)
        self.width = 100
        # Round 34: the TUI hands its live window width here — command bodies (/effort, /model notes…) were
        # rendered at the start-up 100 columns and broke mid-word in a narrower or resized window.
        self.width_of: Optional[Callable[[], int]] = None

    def set_palette(self, palette: Palette) -> None:
        from .theme import rich_theme
        self.palette = palette
        self.theme = rich_theme(palette)

    # ------------------------------------------------------------ core
    def render(self, *items: RenderableType, width: Optional[int] = None) -> str:
        buffer = io.StringIO()
        console = Console(file=buffer, force_terminal=self.color_system is not None, color_system=self.color_system,
                          width=max(20, (width or self._live_width())), height=25, theme=self.theme, legacy_windows=False,
                          emoji=False, highlight=False, soft_wrap=False, markup=False)
        for item in items:
            console.print(item)
        return buffer.getvalue()

    def _live_width(self) -> int:
        if self.width_of is not None:
            try:
                return int(self.width_of())
            except Exception:
                pass
        return self.width

    def md(self, text: str) -> Any:
        return markdown_class()(text, code_theme=self.palette.code_theme, hyperlinks=False)

    def prefixed(self, body: str, first: str, rest: str = "  ") -> str:
        """Prefix the first non-empty line with ``first`` and indent the others."""
        lines = body.rstrip("\n").split("\n")
        out, done = [], False
        for line in lines:
            if not done and _strip_ansi(line).strip():
                out.append(first + line)
                done = True
            else:
                out.append(rest + line)
        return "\n".join(out) + "\n"

    # ------------------------------------------------------------ messages
    def assistant(self, text: str, width: Optional[int] = None) -> str:
        text = text.strip()
        if not text:
            return ""
        return self.assistant_chunk(text, True, width)

    def assistant_chunk(self, text: str, first: bool, width: Optional[int] = None) -> str:
        """A committed part of a streaming answer."""
        width = width or self.width
        body = self.render(self.md(text), width=max(10, width - 2))
        if first:
            return self.prefixed(body, self._bullet_prefix())
        return self.prefixed(body, "  ")

    def _bullet_prefix(self) -> str:
        cached = getattr(self, "_bullet_cache", None)
        if cached is None or cached[0] is not self.theme:
            cached = (self.theme, self.render(Text(BULLET + " ", style="text"), width=4).rstrip("\n"))
            self._bullet_cache = cached
        return cached[1]

    def user(self, text: str, *, images: int = 0, kind: str = "prompt", queued: bool = False,
             width: Optional[int] = None, expanded: bool = False, limit: int = 14) -> str:
        """The user's message on a gray band; a long one folds to its first lines."""
        style = "user"
        marker = {"bash": "!", "memory": "#"}.get(kind, ">")
        width = width or self.width
        lines = (text or "").rstrip().split("\n") or [""]
        folded = 0
        if not expanded and len(lines) > limit:
            folded = len(lines) - (limit - 2)
            lines = lines[:limit - 2]
        elif len(lines) > 400:
            folded = len(lines) - 400
            lines = lines[:400]
        rows = []
        for index, line in enumerate(lines):
            prefix = f"{marker} " if index == 0 else "  "
            rows.append(Text(prefix + line, style=style if not queued else "dim"))
        if folded:
            more = Text("  " + (f"▸ … +{folded} lines — click to expand" if not expanded else f"… +{folded} more lines"),
                        style="user" if not queued else "dim")
            rows.append(more)
        elif expanded and len(lines) > limit:
            rows.append(Text("  ▾ click to fold", style=style))
        if images:
            rows.append(Text("  " + (f"[{images} image]" if images == 1 else f"[{images} images]"), style="dim"))
        rendered = self.render(*[_pad(row, width) for row in rows], width=width)
        return "\n" + rendered

    def thinking(self, text: str, seconds: float, *, full: bool = False, expanded: bool = False,
                 width: Optional[int] = None) -> str:
        label = f"Thought for {seconds:.0f}s" if seconds >= 1 else "Thinking"
        has_text = bool(text.strip())
        show = full or expanded
        head = Text()
        head.append("  " + ("▾ " if show and has_text else ("▸ " if has_text else "  ")), style="subtle")
        head.append(f"✻ {label}", style="dim italic")
        if not show or not has_text:
            return self.render(head, width=width)
        body = Text(text.strip()[-6000:], style="dim italic")
        return self.render(head, Padding(body, (0, 0, 0, 4)), width=width)

    def activity(self, steps: Sequence[Any], expanded: bool, width: Optional[int] = None) -> str:
        from .activity import ActivityView
        return ActivityView(self).render(steps, expanded, width or self.width)

    def notice(self, text: str, level: str = "info", width: Optional[int] = None) -> str:
        style = {"warn": "warn", "error": "err", "success": "ok", "info": "dim"}.get(level, "dim")
        mark = {"warn": "⚠", "error": "✗", "success": "✓"}.get(level, "·")
        # Round 34: a grid keeps the hanging indent — a long second line («… · /auto-continue cancel») wrapped
        # back to column 0. Continuation lines written as "\n  …" lose their own two spaces (the column gives them).
        lines = str(text).split("\n")
        body = "\n".join([lines[0]] + [line[2:] if line.startswith("  ") else line for line in lines[1:]])
        grid = Table.grid(padding=0)
        grid.add_column(width=2, no_wrap=True)
        grid.add_column(overflow="fold")
        grid.add_row(Text(f"{mark} ", style=style), Text(body, style=style))
        return self.render(grid, width=width)

    def error(self, title: str, detail: str = "") -> str:
        head = Table.grid(padding=0)
        head.add_column(width=2, no_wrap=True)
        head.add_column(overflow="fold")
        head.add_row(Text(f"{BULLET} ", style="err"), Text(title, style="err"))
        rows: List[RenderableType] = [head]
        if detail:
            # A grid keeps a hanging indent: a long upstream message wrapped back to column 0 before.
            grid = Table.grid(padding=0)
            grid.add_column(width=5, no_wrap=True)
            grid.add_column(overflow="fold")
            for index, line in enumerate(detail.strip().splitlines()[:12]):
                grid.add_row(Text(f"  {ELBOW}  " if index == 0 else "     ", style="dim"), Text(line, style="dim"))
            rows.append(grid)
        return self.render(*rows)

    # ------------------------------------------------------------ tools
    def tool_header(self, call: Any, status: str = "done") -> Text:
        color = {"done": "ok", "error": "err", "denied": "warn", "cancelled": "warn", "running": "dim",
                 "pending": "dim"}.get(status, "dim")
        text = Text()
        text.append(BULLET + " ", style=color)
        text.append(tool_title(call.name), style="tool")
        if call.display:
            text.append(f"({call.display})", style="tool.args")
        return text

    def tool_block(self, call: Any, result: Any, *, verbose: bool = False, width: Optional[int] = None) -> str:
        status = call.status if call.status in ("denied", "cancelled") else ("done" if result.ok else "error")
        parts: List[RenderableType] = [self.tool_header(call, status)]
        parts.extend(self._tool_body(call, result, verbose=verbose))
        return self.render(*parts, width=width)

    def _elbow(self, text: str, style: str = "dim") -> Text:
        return Text(f"  {ELBOW}  {text}", style=style)

    def _tool_body(self, call: Any, result: Any, *, verbose: bool) -> List[RenderableType]:
        rows: List[RenderableType] = []
        if call.status == "denied":
            reason = (result.summary or result.error or tr("Отклонено", "Denied"))
            rows.append(self._elbow(reason, "warn"))
            return rows
        if not result.ok:
            message = (result.error or result.summary or tr("Ошибка", "Error")).strip()
            lines = message.splitlines()
            rows.append(self._elbow(lines[0][:300], "err"))
            for line in lines[1:6 if not verbose else 40]:
                rows.append(Text("     " + line[:300], style="err"))
            if call.name == "Bash" and result.detail:
                rows.extend(self._output_tail(result.detail, 12 if not verbose else 200))
            return rows
        name = call.name
        if name == "TodoWrite":
            todos = (result.meta or {}).get("todos") or []
            rows.append(self._elbow(result.summary))
            for todo in todos[:30]:
                rows.append(self.todo_line(todo, indent=5))
            return rows
        rows.append(self._elbow(result.summary or tr("Готово", "Done")))
        if result.diff:
            rows.append(Padding(self.diff(result.diff, max_lines=None if verbose else 40), (0, 0, 0, 5)))
        elif name == "Write" and result.detail:
            rows.extend(self._output_tail(result.detail, 200 if verbose else 8, head=True))
        elif name in ("Bash", "BashOutput") and result.detail:
            rows.extend(self._output_tail(result.detail, 200 if verbose else 8))
        elif verbose and result.detail:
            rows.extend(self._output_tail(result.detail, 60, head=True))
        return rows

    def _output_tail(self, text: str, limit: int, *, head: bool = False) -> List[RenderableType]:
        lines = [line.rstrip() for line in text.rstrip().splitlines()]
        if not lines:
            return []
        rows: List[RenderableType] = []
        if len(lines) > limit:
            hidden = len(lines) - limit
            shown = lines[:limit] if head else lines[-limit:]
            note = Text("     " + tr(f"… ещё {hidden} строк (ctrl+o — подробно)",
                                    f"… +{hidden} lines (ctrl+o for verbose output)"), style="subtle")
            if head:
                rows.extend(Text("     " + line[:400], style="dim") for line in shown)
                rows.append(note)
            else:
                rows.append(note)
                rows.extend(Text("     " + line[:400], style="dim") for line in shown)
            return rows
        return [Text("     " + line[:400], style="dim") for line in lines]

    def todo_line(self, todo: Dict[str, Any], indent: int = 2) -> Text:
        status = todo.get("status")
        mark = {"completed": "☒", "in_progress": "◼", "pending": "☐"}.get(status, "☐")
        style = {"completed": "dim strike", "in_progress": "bold brand", "pending": "text"}.get(status, "text")
        return Text(" " * indent + f"{mark} {todo.get('content', '')}", style=style)

    # ------------------------------------------------------------ diffs
    def diff(self, diff: str, *, max_lines: Optional[int] = 40) -> RenderableType:
        rows: List[Text] = []
        old = new = 0
        width = max(20, self.width - 8)
        count = 0
        total_lines = 0
        for line in diff.splitlines():
            if line.startswith(("---", "+++", "diff --git", "index ", "new file", "deleted file")):
                continue
            hunk = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$", line)
            if hunk:
                old, new = int(hunk.group(1)), int(hunk.group(2))
                if rows:
                    rows.append(Text("     ⋮", style="subtle"))
                continue
            total_lines += 1
            if max_lines is not None and count >= max_lines:
                continue
            count += 1
            if line.startswith("+"):
                rows.append(_pad(Text(f"{new:>5} + {line[1:]}", style="diff.add"), width))
                new += 1
            elif line.startswith("-"):
                rows.append(_pad(Text(f"{old:>5} - {line[1:]}", style="diff.del"), width))
                old += 1
            else:
                body = line[1:] if line.startswith(" ") else line
                text = Text(f"{new:>5}   ", style="diff.lineno")
                text.append(body, style="text")
                rows.append(text)
                old += 1
                new += 1
        if max_lines is not None and total_lines > max_lines:
            rows.append(Text(tr(f"     … ещё {total_lines - max_lines} строк диффа (ctrl+o)",
                                f"     … +{total_lines - max_lines} diff lines (ctrl+o)"), style="subtle"))
        return Group(*rows) if rows else Text(tr("     (без изменений)", "     (no changes)"), style="dim")

    def diff_panel(self, diff: str, title: str = "", max_lines: Optional[int] = 60) -> str:
        return self.render(Panel(self.diff(diff, max_lines=max_lines), title=title, title_align="left",
                                 border_style="subtle", box=box.ROUNDED, padding=(0, 1)))

    # ------------------------------------------------------------ building blocks
    def panel(self, body: RenderableType, title: str = "", style: str = "subtle") -> str:
        return self.render(Panel(body, title=title, title_align="left", border_style=style, box=box.ROUNDED,
                                 padding=(0, 1)))

    def table(self, headers: Sequence[str], rows: Iterable[Sequence[Any]], *, title: str = "",
              justify: Optional[Sequence[str]] = None) -> Table:
        table = Table(box=box.SIMPLE_HEAD, show_edge=False, pad_edge=False, header_style="bold",
                      title=title or None, title_justify="left", title_style="bold")
        for index, header in enumerate(headers):
            table.add_column(header, justify=(justify[index] if justify else "left"), overflow="fold")
        for row in rows:
            table.add_row(*[cell if isinstance(cell, (Text, Table, Group)) else str(cell) for cell in row])
        return table

    def gradient_text(self, text: str, bold: bool = True) -> Text:
        colors = gradient(self.palette.brand, self.palette.brand2, max(2, len(text))) \
            if not self.palette.ansi else [self.palette.brand] * len(text)
        out = Text()
        for char, color in zip(text, colors):
            out.append(char, style=("bold " if bold else "") + (color.replace("ansi", "") if self.palette.ansi else color))
        return out

    def logo(self) -> Group:
        art = ["▗▄▖  ▗▄▖", "▐██▙▟██▌", "▐█▌▜▛▐█▌", "▝▀▘  ▝▀▘"]
        return Group(*[self.gradient_text(line, bold=False) for line in art])

    def meter(self, share: float, width: int = 20) -> Text:
        share = max(0.0, min(1.0, share))
        filled = int(round(share * width))
        style = "ok" if share < 0.6 else ("warn" if share < 0.85 else "err")
        text = Text("█" * filled, style=style)
        text.append("░" * (width - filled), style="subtle")
        return text


def _pad(text: Text, width: int) -> Text:
    """Background style across the whole line (user messages, diff rows)."""
    length = text.cell_len
    if length < width:
        text.append(" " * (width - length), style=text.style if isinstance(text.style, str) else "")
    return text


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def strip_ansi(text: str) -> str:
    return _strip_ansi(text)


# ---------------------------------------------------------------- streaming

def split_stable(text: str) -> Tuple[str, str]:
    """(complete markdown blocks, the unfinished tail) of a streaming answer.

    A block is complete after a blank line outside a code fence; a closed
    fence is complete right after its closing line.
    """
    lines = text.split("\n")
    in_fence = False
    fence_mark = ""
    cut = 0
    position = 0
    for index, line in enumerate(lines[:-1]):          # the last line may be partial
        position += len(line) + 1
        stripped = line.strip()
        match = re.match(r"^(`{3,}|~{3,})", stripped)
        if match:
            if not in_fence:
                in_fence, fence_mark = True, match.group(1)
            elif stripped.startswith(fence_mark) and stripped.strip("`~") == "":
                in_fence = False
                cut = position
            continue
        if not in_fence and not stripped:
            cut = position
    return text[:cut], text[cut:]


def live_preview(tail: str, max_lines: int = 18) -> str:
    """The end of the unfinished tail, keeping an open fence's opener for highlighting."""
    lines = tail.split("\n")
    if len(lines) <= max_lines:
        return tail
    opener = ""
    in_fence = False
    for line in lines:
        if re.match(r"^\s*(`{3,}|~{3,})", line):
            in_fence = not in_fence
            opener = line if in_fence else ""
    shown = lines[-max_lines:]
    if in_fence and opener and opener not in shown:
        shown = [opener] + shown[1:]
    return "\n".join(shown)
