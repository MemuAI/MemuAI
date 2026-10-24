"""Colours of the terminal interface.

One palette per theme feeds both renderers: ``rich`` (messages, markdown,
diffs, tables) and ``prompt_toolkit`` (the live area, input box, dialogs,
footer). The brand gradient is the MemuAI terracotta → plum.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from typing import Dict, List, Tuple

ACCENTS = {
    "terracotta": "#d97757", "plum": "#a87fe8", "blue": "#4e9cf5", "cyan": "#2ec4d6", "green": "#4fbf73",
    "yellow": "#e6b422", "orange": "#f08a3c", "red": "#f0566a", "pink": "#ee6fb5", "teal": "#35a89a",
    "indigo": "#7c83f7", "gray": "#9aa0a6",
}


@dataclass
class Palette:
    name: str
    dark: bool
    text: str
    dim: str
    subtle: str
    border: str
    brand: str
    brand2: str
    success: str
    error: str
    warning: str
    info: str
    user_bg: str
    code_theme: str
    add_bg: str
    add_fg: str
    del_bg: str
    del_fg: str
    mode_edits: str = "#af87ff"
    mode_plan: str = "#48a999"
    mode_auto: str = "#e0a526"
    mode_bypass: str = "#ff5f6d"
    bash: str = "#fd5db1"
    memory: str = "#6a9cf5"
    selection: str = "#3a3d4a"
    ansi: bool = False
    extra: Dict[str, str] = field(default_factory=dict)


DARK = Palette(
    name="dark", dark=True, text="#e6e6e6", dim="#8b8b8b", subtle="#5c5c5c", border="#6b6b6b",
    brand="#d97757", brand2="#a87fe8", success="#4eba65", error="#ff6b80", warning="#ffc107", info="#7ab4e8",
    user_bg="#2b2b2b", code_theme="monokai", add_bg="#1d3b26", add_fg="#9be0ad", del_bg="#47202a",
    del_fg="#ffadb6", selection="#3a3d4a")
LIGHT = Palette(
    name="light", dark=False, text="#1f1f1f", dim="#6e6e6e", subtle="#a3a3a3", border="#9a9a9a",
    brand="#c0603f", brand2="#7d52c9", success="#2c7d3d", error="#c81e3c", warning="#8a6100", info="#1d63b8",
    user_bg="#eeeeee", code_theme="friendly", add_bg="#d8f3dd", add_fg="#1c5a2a", del_bg="#fbdde1",
    del_fg="#8a1728", mode_edits="#7a4fd6", mode_plan="#1f7a6d", mode_auto="#9a6b00", mode_bypass="#c81e3c",
    bash="#c2267f", memory="#2f5fcf", selection="#dcdff0")
DARK_DALTONIZED = replace(DARK, name="dark-daltonized", success="#4a9eff", add_bg="#15324f", add_fg="#9cc8ff",
                          error="#ff9e3d", del_bg="#4a3014", del_fg="#ffc68a")
LIGHT_DALTONIZED = replace(LIGHT, name="light-daltonized", success="#0b5cad", add_bg="#dbeafe", add_fg="#0b3d75",
                           error="#b35900", del_bg="#fde9d4", del_fg="#7a3d00")
DARK_ANSI = replace(DARK, name="dark-ansi", text="ansiwhite", dim="ansibrightblack", subtle="ansibrightblack",
                    border="ansibrightblack", brand="ansired", brand2="ansimagenta", success="ansigreen",
                    error="ansibrightred", warning="ansiyellow", info="ansicyan", user_bg="", add_bg="",
                    add_fg="ansigreen", del_bg="", del_fg="ansired", mode_edits="ansimagenta",
                    mode_plan="ansicyan", mode_auto="ansiyellow", mode_bypass="ansibrightred", bash="ansimagenta",
                    memory="ansiblue", selection="", ansi=True)
LIGHT_ANSI = replace(DARK_ANSI, name="light-ansi", dark=False, text="ansiblack", code_theme="friendly")

THEMES = {palette.name: palette for palette in (DARK, LIGHT, DARK_DALTONIZED, LIGHT_DALTONIZED, DARK_ANSI, LIGHT_ANSI)}


def palette_for(name: str, accent: str = "") -> Palette:
    palette = THEMES.get(str(name or "dark"), DARK)
    color = ACCENTS.get(accent, accent if str(accent).startswith("#") and len(accent) == 7 else "")
    if color and not palette.ansi:
        palette = replace(palette, brand=color)
    return palette


# ---------------------------------------------------------------- colour maths

def _rgb(color: str) -> Tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _hex(rgb: Tuple[float, float, float]) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(value))) for value in rgb)


def mix(a: str, b: str, t: float) -> str:
    if not a.startswith("#") or not b.startswith("#"):
        return a
    ra, rb = _rgb(a), _rgb(b)
    return _hex(tuple(ra[i] + (rb[i] - ra[i]) * t for i in range(3)))


def gradient(start: str, end: str, steps: int) -> List[str]:
    if steps <= 1:
        return [start]
    return [mix(start, end, index / (steps - 1)) for index in range(steps)]


def rich_theme(palette: Palette):
    from rich.theme import Theme
    p = palette
    styles = {
        "markdown.h1": f"bold {p.brand}", "markdown.h2": f"bold {p.text}", "markdown.h3": f"bold {p.text}",
        "markdown.h4": f"bold {p.dim}", "markdown.h5": f"italic {p.dim}", "markdown.h6": f"italic {p.dim}",
        "markdown.code": f"{p.info}", "markdown.link": f"underline {p.info}", "markdown.link_url": f"{p.dim}",
        "markdown.block_quote": f"italic {p.dim}", "markdown.item.bullet": f"{p.dim}",
        "markdown.item.number": f"{p.dim}", "markdown.hr": f"{p.subtle}", "markdown.table.border": p.subtle,
        "markdown.table.header": f"bold {p.text}", "markdown.strong": "bold", "markdown.em": "italic",
        "brand": p.brand, "brand2": p.brand2, "dim": p.dim, "subtle": p.subtle, "ok": p.success, "err": p.error,
        "warn": p.warning, "info": p.info, "text": p.text,
        "user": f"{p.text} on {p.user_bg}" if p.user_bg else p.text,
        "diff.add": f"{p.add_fg} on {p.add_bg}" if p.add_bg else p.add_fg,
        "diff.del": f"{p.del_fg} on {p.del_bg}" if p.del_bg else p.del_fg,
        "diff.lineno": p.subtle, "diff.hunk": p.info, "tool": f"bold {p.text}", "tool.args": p.dim,
        "mode.edits": p.mode_edits, "mode.plan": p.mode_plan, "mode.auto": p.mode_auto,
        "mode.bypass": p.mode_bypass, "bash": p.bash, "memory": p.memory,
    }
    if p.ansi:
        styles = {key: value.replace("ansibright", "bright_").replace("ansi", "") for key, value in styles.items()}
    return Theme(styles)


def pt_style(palette: Palette):
    from prompt_toolkit.styles import Style
    p = palette

    def c(value: str) -> str:
        return value.replace("ansibright", "ansibright") if p.ansi else value

    rules = {
        "": f"{c(p.text)} bg:default",
        "header": f"{c(p.dim)} bg:default",
        "transcript.selection": f"bg:{c(p.selection)}" if p.selection else "reverse",
        "brand": c(p.brand), "brand2": c(p.brand2), "dim": c(p.dim), "subtle": c(p.subtle), "text": c(p.text),
        "ok": c(p.success), "err": c(p.error), "warn": c(p.warning), "info": c(p.info),
        "border": c(p.border), "border.focus": c(p.brand),
        "prompt": f"bold {c(p.text)}", "placeholder": f"{c(p.subtle)} italic",
        "input": "", "footer": c(p.dim), "footer.key": f"bold {c(p.dim)}",
        "mode.default": c(p.dim), "mode.acceptedits": c(p.mode_edits), "mode.plan": c(p.mode_plan),
        "mode.auto": c(p.mode_auto), "mode.bypasspermissions": c(p.mode_bypass),
        "mode.bash": c(p.bash), "mode.memory": c(p.memory),
        "completion": c(p.dim), "completion.selected": f"bold {c(p.brand)}", "completion.meta": c(p.subtle),
        "completion.meta.selected": c(p.text), "completion.match": "bold",
        # Override prompt_toolkit's default blue dialog background explicitly.
        "dialog": f"{c(p.text)} bg:default",
        "dialog.border": c(p.brand), "dialog.title": f"bold {c(p.text)}", "dialog.body": c(p.text),
        "dialog.tab.selected": f"bold {c(p.brand)} reverse",
        "dialog.option": c(p.text), "dialog.option.selected": f"bold {c(p.brand)}",
        "dialog.option.desc": c(p.dim), "dialog.hint": c(p.subtle), "dialog.danger": c(p.error),
        "spinner": c(p.brand), "spinner.text": c(p.brand), "spinner.shine": f"bold {c(mix(p.brand, '#ffffff', .45) if not p.ansi else p.brand)}",
        "spinner.meta": c(p.dim), "queued": c(p.dim), "todo.done": f"{c(p.dim)} strike",
        "thinking": f"italic {c(p.dim)}", "thinking.head": f"{c(p.subtle)}",
        "todo.active": f"bold {c(p.brand)}", "todo.pending": c(p.text), "tool.running": c(p.dim),
        "notice.warn": c(p.warning), "notice.err": c(p.error), "notice.ok": c(p.success),
        "search": f"bold {c(p.info)}",
    }
    return Style.from_dict(rules)


def color_depth() -> str:
    """truecolor | 256 | 16 — Windows Terminal and modern consoles support truecolor."""
    if os.environ.get("NO_COLOR"):
        return "none"
    term = os.environ.get("COLORTERM", "").lower()
    if term in ("truecolor", "24bit") or os.environ.get("WT_SESSION") or os.environ.get("TERM_PROGRAM") in (
            "vscode", "iTerm.app", "WezTerm"):
        return "truecolor"
    if os.name == "nt":
        return "truecolor"
    if "256" in os.environ.get("TERM", ""):
        return "256"
    return "truecolor"
