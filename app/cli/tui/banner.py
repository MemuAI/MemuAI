"""The welcome panel at the top of a new session."""
from __future__ import annotations

import getpass
import json
import random
from pathlib import Path
from typing import Any, List

from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .. import VERSION, paths
from ..session import list_sessions

def user_name(runtime: Any) -> str:
    prefs = runtime.memuai_prefs()
    name = str(prefs.get("profile_name") or "").strip()
    if not name:
        try:
            profiles = json.loads((paths.memu_dir() / "profiles.json").read_text(encoding="utf-8"))
            active = profiles.get("active_id") or profiles.get("active")
            for row in profiles.get("profiles") or profiles.get("items") or []:
                if isinstance(row, dict) and row.get("id") == active and row.get("name"):
                    name = str(row["name"])
        except (OSError, ValueError, AttributeError):
            pass
    if not name:
        try:
            name = getpass.getuser()
        except Exception:
            name = ""
    return name.split()[0] if name else ""

TIPS = [
    "/init to study the project and write MEMUCODE.md",
    "/help for commands · ? for shortcuts",
    "Shift+Tab: accept edits, plan, auto",
    "@file attaches a file, !cmd runs a shell command",
    "/model picks a model, /usage shows the cost",
    "/resume continues a previous conversation",
    "Click a gray line to see exactly what was done",
    "/subtask runs a task in parallel while you keep talking",
    "Alt+V pastes a screenshot from the clipboard",
]

class Banner:
    """Gathered once (recent conversations, tips); drawn for whatever width the window has now."""

    def __init__(self, renderer: Any, runtime: Any) -> None:
        from ..timefmt import ago
        self.renderer = renderer
        self.runtime = runtime
        name = user_name(runtime)
        returning = bool(runtime.settings.get("onboarded"))
        if name:
            self.hello = f"Welcome back, {name}!" if returning else f"Welcome, {name}!"
        else:
            self.hello = "Welcome back!" if returning else "Welcome!"
        self.tips = random.sample(TIPS, 3)
        rows: List[Any] = []
        try:

            recent = [row for row in list_sessions(runtime.root, limit=8)
                      if row["id"] != runtime.session.id and str(row.get("title") or row.get("first") or "").strip()][:3]
        except Exception:
            recent = []
        stamps = [ago(row.get("updated", 0)) for row in recent]
        pad = max((len(stamp) for stamp in stamps), default=0) + 2
        for stamp, row in zip(stamps, recent):
            title = " ".join(str(row.get("title") or row.get("first") or "").split())
            rows.append((stamp.ljust(pad), title[:80]))
        self.recent = rows

    def render(self, width: int) -> str:
        renderer, runtime = self.renderer, self.runtime
        root = str(runtime.root)
        home = str(Path.home())
        if root.lower().startswith(home.lower()):
            root = "~" + root[len(home):]
        model = runtime.model or "choosing a model…"
        left_rows: List[Any] = [Text(""), Text(self.hello, style="bold", justify="center"), Text("")]
        logo = renderer.logo()
        left_rows.append(Group(*[Text.assemble(line, justify="center") for line in logo.renderables]))
        panel_width = min(width, 110)
        column = (panel_width - 6) * 5 // 11 if width >= 76 else panel_width - 4
        root = short_path(root, column - 1)
        left_rows += [Text(""), Text(model, style="dim", justify="center"), Text(root, style="dim", justify="center")]
        left = Group(*left_rows)
        tips = Group(Text("Tips for getting started", style="bold brand"), *[Text(tip, style="text") for tip in self.tips])
        if self.recent:
            recent = Group(Text("Recent activity", style="bold brand"),
                           *[Text.assemble((when, "dim"), (title, "text"), no_wrap=True, overflow="ellipsis")
                             for when, title in self.recent],
                           Text("/resume to continue", style="dim"))
        else:
            recent = Group(Text("Recent activity", style="bold brand"), Text("nothing yet", style="dim"))
        title = Text.assemble(("MemuCode", "bold brand"), (f" v{VERSION}", "dim"))
        if width >= 76:
            grid = Table.grid(expand=True, padding=(0, 2))
            grid.add_column(ratio=5)
            grid.add_column(ratio=6)
            grid.add_row(left, Group(tips, Text(""), recent))
            body: Any = grid
        else:
            body = Group(left, Text(""), tips)
        panel = Panel(body, title=title, title_align="left", border_style="brand", box=box.ROUNDED, padding=(0, 1),
                      width=min(width, 110))
        return renderer.render(panel, width=width)

def short_path(path: str, limit: int) -> str:
    """Keep the END of a long path — the project folder is the part that matters.

    Round 31: the banner and the trust dialog cut «/workspace/projects/b68b…/work/scratch-desig…»
    at the right, so the user never saw which folder MemuCode had opened."""
    path = str(path)
    limit = max(8, int(limit))
    if len(path) <= limit:
        return path
    tail = path[-(limit - 1):]
    for sep in ("\\", "/"):
        cut = tail.find(sep)
        if 0 <= cut < len(tail) - 4:
            tail = tail[cut:]
            break
    return "…" + tail

def welcome(renderer: Any, runtime: Any) -> Banner:
    return Banner(renderer, runtime)
