"""What the agent did between two of its messages, as one calm gray line.

Claude Code style: the transcript keeps the conversation readable — the user's
request, the assistant's words — and folds the tool traffic in between into a
single dim summary such as ``Read 3 files, searched for 2 patterns, edited
app.py (+1 −1), ran pytest -q``. Clicking the line (or Ctrl+O) unfolds the
exact details: which files were read, what was searched, the diff of every
edit, the command that was typed and its output. Failures and denials always
stay visible in the summary.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from rich.padding import Padding
from rich.text import Text

from ..i18n import tr

MAX_SUMMARY_COMMAND = 46


@dataclass
class Step:
    """One finished tool call."""
    call: Any
    result: Any
    seconds: float = 0.0


KIND_OF = {
    "Read": "read", "ProjectInfo": "read",
    "Grep": "search", "Glob": "search", "Symbols": "search", "Outline": "search",
    "LS": "list",
    "Edit": "edit", "MultiEdit": "edit", "Write": "create",
    "Bash": "run", "Git": "run", "BashOutput": "shell", "KillShell": "shell",
    "WebFetch": "fetch", "WebSearch": "websearch",
    "Task": "agent", "TaskOutput": "agentcheck",
    "TodoWrite": "todo", "AskUserQuestion": "ask", "ExitPlanMode": "plan", "Skill": "skill",
    "Browser": "browser", "Computer": "computer", "Advisor": "advisor", "Decide": "advisor",
    "__invalid__": "badcall",
}


def classify(name: str) -> str:
    if name.startswith("mcp__"):
        return "mcp"
    return KIND_OF.get(name, "other")


def _short(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:max(1, limit - 1)] + "…"


def _file_name(step: Step) -> str:
    args = step.call.args or {}
    raw = args.get("file_path") or args.get("path") or step.call.display or ""
    return str(raw).replace("\\", "/")


def _base(path: str) -> str:
    path = path.rstrip("/")
    parts = path.split("/")
    return "/".join(parts[-2:]) if len(parts) > 2 else path


def _diff_counts(step: Step) -> Tuple[int, int]:
    diff = getattr(step.result, "diff", "") or ""
    added = sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))
    return added, removed


def _domain(url: str) -> str:
    match = re.match(r"^(?:https?://)?([^/\s]+)", str(url or ""))
    return match.group(1) if match else str(url or "")[:40]


def _pattern(step: Step) -> str:
    args = step.call.args or {}
    return str(args.get("pattern") or args.get("query") or args.get("name") or step.call.display or "")


def phrase(kind: str, steps: Sequence[Step], compact: bool = False) -> str:
    """The text for all steps of one kind ("Read app.py", "Read 3 files")."""
    count = len(steps)
    first = steps[0]
    if kind == "read":
        names = {_file_name(step) for step in steps}
        if len(names) == 1 and not compact:
            return tr("Read ", "Read ") + _base(next(iter(names)))
        return f"Read {len(names)} file" + ("" if len(names) == 1 else "s")
    if kind == "search":
        if count == 1 and not compact:
            return f"Searched for “{_short(_pattern(first), 32)}”"
        return f"Searched for {count} pattern" + ("" if count == 1 else "s")
    if kind == "list":
        if count == 1 and not compact:
            return "Listed " + (_base(_file_name(first)) or "the folder")
        return f"Listed {count} folder" + ("" if count == 1 else "s")
    if kind == "edit":
        files = {_file_name(step) for step in steps}
        added = removed = 0
        for step in steps:
            a, r = _diff_counts(step)
            added, removed = added + a, removed + r
        stat = f" (+{added} −{removed})" if added or removed else ""
        if len(files) == 1 and not compact:
            return f"Edited {_base(next(iter(files)))}{stat}"
        return f"Edited {len(files)} file" + ("" if len(files) == 1 else "s") + stat
    if kind == "create":
        files = {_file_name(step) for step in steps}
        if len(files) == 1 and not compact:
            return f"Created {_base(next(iter(files)))}"
        return f"Created {len(files)} file" + ("" if len(files) == 1 else "s")
    if kind == "run":
        if count == 1 and not compact:
            command = first.call.display or str((first.call.args or {}).get("command") or "")
            return "Ran " + _short(command, MAX_SUMMARY_COMMAND)
        return f"Ran {count} command" + ("" if count == 1 else "s")
    if kind == "shell":
        return "Checked a background command" if count == 1 else f"Checked background commands ×{count}"
    if kind == "fetch":
        if count == 1 and not compact:
            return "Fetched " + _domain(str((first.call.args or {}).get("url") or first.call.display))
        return f"Fetched {count} page" + ("" if count == 1 else "s")
    if kind == "websearch":
        if count == 1 and not compact:
            return f"Searched the web for “{_short(_pattern(first), 34)}”"
        return f"Searched the web {count} times"
    if kind == "agent":
        if count == 1 and not compact:
            return f"Delegated “{_short(str((first.call.args or {}).get('description') or first.call.display), 36)}”"
        return f"Delegated {count} tasks"
    if kind == "agentcheck":
        return "Checked a background agent" if count == 1 else f"Checked background agents ×{count}"
    if kind == "todo":
        return "Updated the todo list"
    if kind == "ask":
        return "Asked a question"
    if kind == "plan":
        return "Proposed a plan"
    if kind == "skill":
        return "Loaded skill " + _short(first.call.display, 30) if count == 1 else f"Loaded {count} skills"
    if kind == "browser":
        return "Used the browser"
    if kind == "computer":
        return "Used the computer"
    if kind == "advisor":
        return "Consulted the advisor"
    if kind == "badcall":
        # A tool call the model wrote badly and sends again: protocol noise, not a failure of the work.
        return "Resent a tool call" if count == 1 else f"Resent tool calls ×{count}"
    if kind == "mcp":
        from .render import tool_title
        return "Called " + _short(tool_title(first.call.name), 40) + ("" if count == 1 else f" ×{count}")
    from .render import tool_title
    return _short(tool_title(first.call.name), 24) + ("" if count == 1 else f" ×{count}")


def in_order(steps: Sequence[Step]) -> List[Step]:
    """Calls finish in any order (parallel reads, agents); the summary follows the order they were issued."""
    return sorted(steps, key=lambda step: getattr(step.call, "seq", 0))


def summary_parts(steps: Sequence[Step], width: int = 100) -> List[Tuple[str, str]]:
    """[(text, style)] of the collapsed line, without the chevron."""
    steps = in_order(steps)
    order: List[str] = []
    groups: Dict[str, List[Step]] = {}
    for step in steps:
        kind = classify(step.call.name)
        if kind not in groups:
            order.append(kind)
            groups[kind] = []
        groups[kind].append(step)
    quiet = [kind for kind in order if kind not in ("todo", "ask", "plan", "badcall")]
    kinds = quiet or order
    failed = [step for step in steps if not step.result.ok and step.call.status not in ("denied", "cancelled")
              and classify(step.call.name) != "badcall"]
    denied = [step for step in steps if step.call.status == "denied"]
    stopped = [step for step in steps if step.call.status == "cancelled"]
    tail: List[Tuple[str, str]] = []
    if failed:
        tail.append((f"  ✗ {len(failed)} failed", "err"))
    if denied:
        tail.append((f"  denied ×{len(denied)}" if len(denied) > 1 else "  denied", "warn"))
    if stopped:
        tail.append(("  stopped", "warn"))
    reserve = sum(len(text) for text, _ in tail) + 6
    for compact in (False, True):
        words = [phrase(kind, groups[kind], compact) for kind in kinds]
        text = ", ".join([words[0]] + [word[:1].lower() + word[1:] for word in words[1:]])
        if len(text) + reserve <= width or compact:
            break
    if len(text) + reserve > width:
        text = _short(text, max(12, width - reserve))
    return [(text, "dim"), *tail]


def _status_text(step: Step) -> str:
    result, call = step.result, step.call
    kind = classify(call.name)
    if call.status == "denied":
        return "denied"
    if not result.ok:
        return _short(result.summary or result.error or "failed", 60)
    if kind == "edit":
        added, removed = _diff_counts(step)
        return f"+{added} −{removed}" if added or removed else _short(result.summary, 40)
    if kind == "run":
        meta = getattr(result, "meta", {}) or {}
        if "exit_code" in meta and meta.get("exit_code") is not None:
            seconds = (meta.get("ms") or 0) / 1000
            return f"exit {meta['exit_code']}" + (f" · {seconds:.1f}s" if seconds else "")
    if kind == "todo":
        todos = (getattr(result, "meta", {}) or {}).get("todos") or []
        done = sum(1 for todo in todos if todo.get("status") == "completed")
        return f"{done}/{len(todos)} done"
    if kind == "agent":
        return _short(result.summary, 60)
    return _short(result.summary or "", 60)


LABELS = {"read": "Read", "search": "Search", "list": "List", "edit": "Edit", "create": "Create", "run": "Run",
          "shell": "Shell", "fetch": "Fetch", "websearch": "Web", "agent": "Agent", "agentcheck": "Agent",
          "todo": "Todos", "ask": "Ask", "plan": "Plan", "skill": "Skill", "browser": "Browser",
          "computer": "Computer", "advisor": "Advisor", "mcp": "MCP", "badcall": "Retry"}


def _arg_text(step: Step) -> str:
    kind = classify(step.call.name)
    args = step.call.args or {}
    if kind in ("read", "edit", "create", "list"):
        return _base(_file_name(step)) or "."
    if kind == "search":
        text = _pattern(step)
        where = args.get("path") or args.get("glob") or args.get("type")
        return f"“{_short(text, 50)}”" + (f" in {where}" if where else "")
    if kind == "run":
        return " ".join(str(args.get("command") or step.call.display).split()[:60])
    if kind == "agent":
        return str(args.get("description") or step.call.display)
    if kind == "todo":
        return ""
    return step.call.display or ""


def _output_lines(text: str, limit: int, *, tail: bool = False) -> Tuple[List[str], int]:
    lines = [line.rstrip() for line in str(text or "").rstrip().splitlines()]
    hidden = max(0, len(lines) - limit)
    if hidden:
        lines = lines[-limit:] if tail else lines[:limit]
    return lines, hidden


class ActivityView:
    """Builds the rich renderables of an activity block."""

    def __init__(self, renderer: Any) -> None:
        self.renderer = renderer

    def header(self, steps: Sequence[Step], expanded: bool, width: int) -> Text:
        text = Text()
        text.append("  ")
        text.append("▾ " if expanded else "▸ ", style="subtle")
        for part, style in summary_parts(steps, width - 4):
            text.append(part, style=style)
        text.no_wrap = True
        text.overflow = "ellipsis"
        return text

    def steps_renderables(self, steps: Sequence[Step], width: int) -> List[Any]:
        rows: List[Any] = []
        for step in in_order(steps):
            rows.extend(self._step(step, width))
        return rows

    def _step(self, step: Step, width: int) -> List[Any]:
        kind = classify(step.call.name)
        label = LABELS.get(kind) or step.call.name
        arg = _arg_text(step)
        status = _status_text(step)
        failed = not step.result.ok
        style = "err" if failed and step.call.status != "denied" else ("warn" if step.call.status == "denied" else "dim")
        if kind == "badcall":
            style = "warn"
        room = max(10, width - 8 - len(label) - len(status) - 4)
        line = Text()
        line.append("    ")
        line.append(f"{label:<7}", style="tool")
        line.append(_short(arg, room), style="text" if kind in ("edit", "read", "create") else "dim")
        if status:
            line.append("  " + status, style=style if failed else "subtle")
        rows: List[Any] = [line]
        rows.extend(self._body(step, kind, width))
        return rows

    def _dim(self, text: str, indent: int = 8, style: str = "dim") -> Text:
        return Text(" " * indent + text, style=style, no_wrap=True, overflow="ellipsis")

    def _body(self, step: Step, kind: str, width: int) -> List[Any]:
        result, call = step.result, step.call
        rows: List[Any] = []
        if call.status == "denied":
            reason = (result.summary or result.error or "").strip()
            if reason and reason.lower() != "denied":
                rows.append(self._dim(reason, style="warn"))
            return rows
        if not result.ok:
            message = (result.error or result.summary or "failed").strip()
            lines, hidden = _output_lines(message, 6)
            rows.extend(self._dim(line[:300], style="warn" if kind == "badcall" else "err") for line in lines)
            if hidden:
                rows.append(self._dim(f"… +{hidden} lines", style="subtle"))
            if kind == "run" and result.detail:
                lines, hidden = _output_lines(result.detail, 12, tail=True)
                if hidden:
                    rows.append(self._dim(f"… +{hidden} earlier lines", style="subtle"))
                rows.extend(self._dim(line[:400]) for line in lines)
            return rows
        if kind == "edit" and result.diff:
            rows.append(Padding(self.renderer.diff(result.diff, max_lines=40), (0, 0, 0, 2)))
        elif kind == "create":
            lines, hidden = _output_lines(result.detail or "", 10)
            rows.extend(self._dim(f"{number:>4}  {line[:300]}") for number, line in enumerate(lines, 1))
            if hidden:
                rows.append(self._dim(f"… +{hidden} lines", style="subtle"))
        elif kind == "run":
            command = str((call.args or {}).get("command") or "")
            extra = command.splitlines()
            if len(extra) > 1:
                rows.extend(self._dim("$ " + line[:300], style="text") for line in extra[:8])
            lines, hidden = _output_lines(result.detail or "", 12, tail=True)
            if hidden:
                rows.append(self._dim(f"… +{hidden} earlier lines", style="subtle"))
            rows.extend(self._dim(line[:400]) for line in lines)
        elif kind in ("search", "list", "fetch", "websearch", "shell"):
            lines, hidden = _output_lines(result.detail or "", 8)
            rows.extend(self._dim(line[:300]) for line in lines)
            if hidden:
                rows.append(self._dim(f"… +{hidden} more", style="subtle"))
        elif kind == "todo":
            for todo in ((getattr(result, "meta", {}) or {}).get("todos") or [])[:30]:
                rows.append(Padding(self.renderer.todo_line(todo, indent=0), (0, 0, 0, 8)))
        elif kind in ("agent", "agentcheck"):
            report = result.output if isinstance(result.output, str) else str((result.output or {}).get("report") or "")
            lines, hidden = _output_lines(report, 18)
            rows.extend(self._dim(line[:300]) for line in lines)
            if hidden:
                rows.append(self._dim(f"… +{hidden} lines", style="subtle"))
        elif result.detail:
            lines, hidden = _output_lines(result.detail, 8)
            rows.extend(self._dim(line[:300]) for line in lines)
            if hidden:
                rows.append(self._dim(f"… +{hidden} more", style="subtle"))
        return rows

    def problem_lines(self, steps: Sequence[Step], width: int) -> List[Any]:
        """A folded summary still says what went wrong: one red or yellow line per failed/denied step."""
        rows: List[Any] = []
        problems = [step for step in steps if (not step.result.ok or step.call.status in ("denied", "cancelled"))
                    and classify(step.call.name) != "badcall"]
        for step in problems[:3]:
            denied = step.call.status in ("denied", "cancelled")
            label = LABELS.get(classify(step.call.name)) or step.call.name
            reason = _status_text(step) if not denied else (
                "denied" if step.call.status == "denied" else "stopped")
            detail = _short(step.result.error or step.result.summary or "", max(20, width - 24)) if not denied else ""
            line = Text("      ⎿ ", style="subtle", no_wrap=True, overflow="ellipsis")
            if len(steps) == 1:
                # Round 34: a one-step group's header already names the command and says «stopped»/«failed» —
                # the line under it repeated both («Ran for i… stopped / ⎿ Run for i… stopped»). Only a real
                # message (exit code, error text) is added.
                if denied or not detail or detail == reason:
                    continue
                line.append(detail, style="err")
                rows.append(line)
                continue
            line.append(f"{label} {_short(_arg_text(step), 40)}".rstrip(), style="dim")
            line.append("  " + (detail if detail and detail != reason else reason), style="warn" if denied else "err")
            rows.append(line)
        if len(problems) > 3:
            rows.append(Text(f"        … +{len(problems) - 3} more", style="subtle"))
        return rows

    def render(self, steps: Sequence[Step], expanded: bool, width: int) -> str:
        parts: List[Any] = [self.header(steps, expanded, width)]
        parts.extend(self.steps_renderables(steps, width) if expanded else self.problem_lines(steps, width))
        return self.renderer.render(*parts, width=width)
