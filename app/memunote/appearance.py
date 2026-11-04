"""MemuNote appearance: colour palettes, accents and designs.

MemuNote follows MemuAI by default (``"auto"`` everywhere): the light/dark mode, the
surface variant, the accent colour and the design. Each of the three can also be
chosen in MemuNote alone:

* ``palette``  — a named colour palette with a dark and a light face. Most map onto
  MemuAI's own variants (``app/themes.py`` stays the source of those colours); the
  editor-classic ones that MemuAI does not have (GitHub, Gruvbox, Monokai, One,
  Tokyo Night, Sepia) are described here in full, syntax colours included.
* ``accent``   — ``auto`` (MemuAI's accent), ``palette`` (the palette's own colour),
  one of MemuAI's named accents or ``custom`` with ``accent_custom`` = ``#rrggbb``.
  Every accent is fitted to the background so links and active states stay readable.
* ``design``   — the shape language of the window (CSS in ``static/designs.css``):
  corners, chrome, density, glass and so on. ``auto`` maps MemuAI's design.

Stdlib only: MemuNote ships alone. ``app.themes`` is bundled with it, but every use is
guarded so a missing module only drops back to the stylesheet defaults.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# MemuAI variants per mode, or None for a palette described below in OWN.
PALETTES: List[Dict[str, Any]] = [
    {"id": "auto", "ru": "Как в MemuAI", "en": "Same as MemuAI"},
    {"id": "classic", "ru": "Классика", "en": "Classic", "dark": "", "light": "",
     "accent": {"dark": "#efa58b", "light": "#a84b30"}},
    {"id": "dim", "ru": "Сумерки", "en": "Dimmed", "dark": "dim", "light": "cool",
     "accent": {"dark": "#90baff", "light": "#2360bc"}},
    {"id": "midnight", "ru": "Полночь", "en": "Midnight", "dark": "midnight", "light": "cool",
     "accent": {"dark": "#82aaff", "light": "#2a55c4"}},
    {"id": "nord", "ru": "Норд", "en": "Nord", "dark": "nord", "light": "cool",
     "accent": {"dark": "#88c0d0", "light": "#4c6f9c"}},
    {"id": "catppuccin", "ru": "Катпучино", "en": "Catppuccin", "dark": "mocha", "light": "latte",
     "accent": {"dark": "#cba6f7", "light": "#7a2ee0"}},
    {"id": "dracula", "ru": "Дракула", "en": "Dracula", "dark": "dusk", "light": "latte",
     "accent": {"dark": "#bd93f9", "light": "#6d3fc4"}},
    {"id": "solarized", "ru": "Солярис", "en": "Solarized", "dark": "solar", "light": "solar",
     "accent": {"dark": "#4aa3e8", "light": "#1b64a0"}},
    {"id": "warm", "ru": "Тепло и бумага", "en": "Warm paper", "dark": "warm", "light": "paper",
     "accent": {"dark": "#f0bf72", "light": "#875710"}},
    {"id": "slate", "ru": "Сланец", "en": "Slate", "dark": "slate", "light": "slate",
     "accent": {"dark": "#a5b2ff", "light": "#3a4bb3"}},
    {"id": "rose", "ru": "Розовый", "en": "Rosé", "dark": "rose", "light": "rose",
     "accent": {"dark": "#ebbcba", "light": "#a4506a"}},
    {"id": "evergreen", "ru": "Хвоя", "en": "Evergreen", "dark": "evergreen", "light": "evergreen",
     "accent": {"dark": "#93d8a6", "light": "#2f6b33"}},
    {"id": "contrast", "ru": "Контраст", "en": "High contrast", "dark": "contrast", "light": "contrast",
     "accent": {"dark": "#ffd54f", "light": "#0b4fc0"}},
    {"id": "github", "ru": "GitHub", "en": "GitHub", "dark": None, "light": None,
     "accent": {"dark": "#58a6ff", "light": "#0969da"}},
    {"id": "gruvbox", "ru": "Грувбокс", "en": "Gruvbox", "dark": None, "light": None,
     "accent": {"dark": "#fe8019", "light": "#af3a03"}},
    {"id": "monokai", "ru": "Монокай", "en": "Monokai", "dark": None, "light": None,
     "accent": {"dark": "#a6e22e", "light": "#b2325a"}},
    {"id": "one", "ru": "One", "en": "One", "dark": None, "light": None,
     "accent": {"dark": "#61afef", "light": "#2f62d0"}},
    {"id": "tokyo", "ru": "Токийская ночь", "en": "Tokyo Night", "dark": None, "light": None,
     "accent": {"dark": "#7aa2f7", "light": "#2959c9"}},
    {"id": "sepia", "ru": "Сепия", "en": "Sepia", "dark": None, "light": None,
     "accent": {"dark": "#e3a86b", "light": "#8b4a1c"}},
]

_SYN = ("kw", "type", "str", "com", "num", "fn", "op", "lit", "tag", "attr", "prop", "var", "meta", "punct")

def _syn(*values: str) -> Dict[str, str]:
    return {f"--tok-{key}": value for key, value in zip(_SYN, values)}

# Complete token sets for the palettes MemuAI does not have.
OWN: Dict[str, Dict[str, Dict[str, str]]] = {
    "github": {
        "dark": {"--bg": "#0d1117", "--bg-deep": "#010409", "--panel": "#161b22", "--panel-2": "#21262d",
                 "--border": "#30363d", "--control-border": "#6e7681", "--text": "#e6edf3", "--muted": "#9da7b3",
                 "--on-accent": "#0d1117", "--danger": "#ff7b72", "--ok": "#3fb950", "--code-bg": "#010409",
                 "--code-text": "#e6edf3",
                 **_syn("#ff7b72", "#ffa657", "#a5d6ff", "#8b949e", "#79c0ff", "#d2a8ff", "#e6edf3", "#79c0ff",
                        "#7ee787", "#79c0ff", "#79c0ff", "#ffa657", "#ff7b72", "#c9d1d9")},
        "light": {"--bg": "#ffffff", "--bg-deep": "#f6f8fa", "--panel": "#ffffff", "--panel-2": "#eff2f5",
                  "--border": "#d0d7de", "--control-border": "#6e7781", "--text": "#1f2328", "--muted": "#59636e",
                  "--on-accent": "#ffffff", "--danger": "#cf222e", "--ok": "#1a7f37", "--code-bg": "#f6f8fa",
                  "--code-text": "#1f2328",
                  **_syn("#cf222e", "#953800", "#0a3069", "#59636e", "#0550ae", "#8250df", "#24292f", "#0550ae",
                         "#116329", "#0550ae", "#0550ae", "#953800", "#cf222e", "#57606a")},
    },
    "gruvbox": {
        "dark": {"--bg": "#282828", "--bg-deep": "#1d2021", "--panel": "#32302f", "--panel-2": "#3c3836",
                 "--border": "#504945", "--control-border": "#928374", "--text": "#ebdbb2", "--muted": "#bdae93",
                 "--on-accent": "#1d2021", "--danger": "#fb4934", "--ok": "#b8bb26", "--code-bg": "#1d2021",
                 "--code-text": "#ebdbb2",
                 **_syn("#fb5b48", "#fabd2f", "#b8bb26", "#a89984", "#d3869b", "#8ec07c", "#fe8019", "#d3869b",
                        "#83a598", "#8ec07c", "#83a598", "#83a598", "#fe8019", "#a89984")},
        "light": {"--bg": "#fbf1c7", "--bg-deep": "#f2e5bc", "--panel": "#f9f5d7", "--panel-2": "#ebdbb2",
                  "--border": "#d5c4a1", "--control-border": "#7c6f64", "--text": "#3c3836", "--muted": "#5f554e",
                  "--on-accent": "#fbf1c7", "--danger": "#9d0006", "--ok": "#79740e", "--code-bg": "#f2e5bc",
                  "--code-text": "#3c3836",
                  **_syn("#9d0006", "#8f5a0a", "#5f5b0b", "#6d6158", "#8f3f71", "#36664a", "#a33603", "#8f3f71",
                         "#076678", "#36664a", "#076678", "#076678", "#a33603", "#6d6158")},
    },
    "monokai": {
        "dark": {"--bg": "#272822", "--bg-deep": "#1e1f1c", "--panel": "#2f302a", "--panel-2": "#3e3d32",
                 "--border": "#49483e", "--control-border": "#8a8672", "--text": "#f8f8f2", "--muted": "#bdbaa8",
                 "--on-accent": "#1e1f1c", "--danger": "#ff6e97", "--ok": "#a6e22e", "--code-bg": "#1e1f1c",
                 "--code-text": "#f8f8f2",
                 **_syn("#fa518e", "#66d9ef", "#e6db74", "#a59f85", "#ae81ff", "#a6e22e", "#fa518e", "#ae81ff",
                        "#fa518e", "#a6e22e", "#66d9ef", "#f8f8f2", "#fd971f", "#cfcfc2")},
        "light": {"--bg": "#faf4f2", "--bg-deep": "#f1ebe9", "--panel": "#ffffff", "--panel-2": "#ede7e5",
                  "--border": "#d9d1ce", "--control-border": "#847c79", "--text": "#29242a", "--muted": "#5e5759",
                  "--on-accent": "#ffffff", "--danger": "#b8213f", "--ok": "#2f6e1a", "--code-bg": "#f3ede9",
                  "--code-text": "#29242a",
                  **_syn("#c7204f", "#126579", "#7a5000", "#6b6466", "#5c46a8", "#1d7350", "#c7204f", "#5c46a8",
                         "#c7204f", "#1d7350", "#126579", "#29242a", "#a8461a", "#6b6466")},
    },
    "one": {
        "dark": {"--bg": "#282c34", "--bg-deep": "#21252b", "--panel": "#2c313a", "--panel-2": "#333842",
                 "--border": "#3e4451", "--control-border": "#7f848e", "--text": "#dcdfe4", "--muted": "#a5abb6",
                 "--on-accent": "#21252b", "--danger": "#f0868f", "--ok": "#98c379", "--code-bg": "#21252b",
                 "--code-text": "#dcdfe4",
                 **_syn("#c678dd", "#e5c07b", "#98c379", "#9197a3", "#d19a66", "#61afef", "#56b6c2", "#d19a66",
                        "#e2737c", "#d19a66", "#e2737c", "#e2737c", "#c678dd", "#abb2bf")},
        "light": {"--bg": "#fafafa", "--bg-deep": "#f0f0f1", "--panel": "#ffffff", "--panel-2": "#eaeaeb",
                  "--border": "#dbdbdc", "--control-border": "#7e8087", "--text": "#383a42", "--muted": "#5c5f68",
                  "--on-accent": "#ffffff", "--danger": "#c91243", "--ok": "#3c7a2a", "--code-bg": "#f2f2f3",
                  "--code-text": "#383a42",
                  **_syn("#a626a4", "#865b00", "#3a763a", "#666a77", "#865b00", "#3166d6", "#0172a3", "#865b00",
                         "#c03a2f", "#865b00", "#c03a2f", "#c03a2f", "#a626a4", "#4f525d")},
    },
    "tokyo": {
        "dark": {"--bg": "#1a1b26", "--bg-deep": "#16161e", "--panel": "#1f2335", "--panel-2": "#292e42",
                 "--border": "#3b4261", "--control-border": "#737aa2", "--text": "#c8d3f5", "--muted": "#9aa5ce",
                 "--on-accent": "#16161e", "--danger": "#f7768e", "--ok": "#9ece6a", "--code-bg": "#16161e",
                 "--code-text": "#c0caf5",
                 **_syn("#bb9af7", "#2ac3de", "#9ece6a", "#8189b3", "#ff9e64", "#7aa2f7", "#89ddff", "#ff9e64",
                        "#f7768e", "#bb9af7", "#7dcfff", "#c0caf5", "#bb9af7", "#a9b1d6")},
        "light": {"--bg": "#e6e7ed", "--bg-deep": "#dadbe2", "--panel": "#f2f3f7", "--panel-2": "#d9dbe4",
                  "--border": "#c4c8da", "--control-border": "#6e7394", "--text": "#2e3350", "--muted": "#4f5575",
                  "--on-accent": "#ffffff", "--danger": "#b31b44", "--ok": "#385f0d", "--code-bg": "#dfe0e7",
                  "--code-text": "#2e3350",
                  **_syn("#6f3fb5", "#005e75", "#435a2b", "#5c5f80", "#844800", "#2853bb", "#005e75", "#844800",
                         "#b31b44", "#6f3fb5", "#0a5f7c", "#2e3350", "#6f3fb5", "#474c6b")},
    },
    "sepia": {
        "dark": {"--bg": "#2a241c", "--bg-deep": "#211c15", "--panel": "#322b22", "--panel-2": "#3c3429",
                 "--border": "#4d4436", "--control-border": "#8a7d66", "--text": "#efe4cf", "--muted": "#c2b499",
                 "--on-accent": "#211c15", "--danger": "#f2a48f", "--ok": "#b9cf8a", "--code-bg": "#211c15",
                 "--code-text": "#e9dcc3",
                 **_syn("#e0a3c0", "#e8c57a", "#b9cf8a", "#a89a80", "#f0b080", "#9cc4de", "#d6c8ad", "#f0b080",
                        "#f2a48f", "#e8c57a", "#9cc4de", "#efe4cf", "#e0a3c0", "#bfb196")},
        "light": {"--bg": "#f4ecd8", "--bg-deep": "#ebe0c7", "--panel": "#faf5e8", "--panel-2": "#ece2cb",
                  "--border": "#d8cbab", "--control-border": "#7f7255", "--text": "#3b2f1e", "--muted": "#5f5139",
                  "--on-accent": "#faf5e8", "--danger": "#9c2a1a", "--ok": "#4a661d", "--code-bg": "#eee4cd",
                  "--code-text": "#3b2f1e",
                  **_syn("#862d5b", "#6f4b00", "#45601b", "#6e5f44", "#86461a", "#2b5774", "#54452e", "#86461a",
                         "#9c2a1a", "#6f4b00", "#2b5774", "#3b2f1e", "#862d5b", "#54452e")},
    },
}

_FALLBACK_ACCENTS: Dict[str, Dict[str, str]] = {
    "terracotta": {"ru": "Терракота", "en": "Terracotta", "light": "#a84b30", "dark": "#efa58b"},
    "blue": {"ru": "Океан", "en": "Ocean", "light": "#2360bc", "dark": "#90baff"},
    "violet": {"ru": "Ирис", "en": "Iris", "light": "#7151b8", "dark": "#c4a7f5"},
    "teal": {"ru": "Нефрит", "en": "Jade", "light": "#167264", "dark": "#7fd2be"},
    "rose": {"ru": "Роза", "en": "Rose", "light": "#ab4567", "dark": "#f2a1bb"},
    "graphite": {"ru": "Графит", "en": "Graphite", "light": "#525e70", "dark": "#bac5d5"},
    "amber": {"ru": "Янтарь", "en": "Amber", "light": "#875710", "dark": "#f0bf72"},
    "forest": {"ru": "Лес", "en": "Forest", "light": "#2f6b33", "dark": "#97d49b"},
    "indigo": {"ru": "Индиго", "en": "Indigo", "light": "#3a4bb3", "dark": "#a5b2ff"},
    "plum": {"ru": "Слива", "en": "Plum", "light": "#8a3d86", "dark": "#e4a4df"},
    "crimson": {"ru": "Кармин", "en": "Crimson", "light": "#b03636", "dark": "#f4a09a"},
    "cyan": {"ru": "Лагуна", "en": "Cyan", "light": "#0f6a86", "dark": "#86cfe6"},
}

def _themes():
    try:
        from .. import themes
        return themes
    except Exception:  # noqa: BLE001 - standalone/dev
        return None

def accents() -> Dict[str, Dict[str, str]]:
    mod = _themes()
    table = getattr(mod, "ACCENTS", None) if mod else None
    return table if isinstance(table, dict) and table else _FALLBACK_ACCENTS

ACCENT_IDS: Tuple[str, ...] = tuple(_FALLBACK_ACCENTS)

DESIGNS: List[Dict[str, str]] = [
    {"id": "auto", "ru": "Как в MemuAI", "en": "Same as MemuAI",
     "note_ru": "Подбирает похожий дизайн к выбранному в MemuAI", "note_en": "Picks the closest match to MemuAI's design"},
    {"id": "classic", "ru": "Классический", "en": "Classic",
     "note_ru": "Вкладки-ушки, тонкие рамки, привычная плотность", "note_en": "Ear tabs, hairlines, the familiar density"},
    {"id": "soft", "ru": "Мягкий", "en": "Soft",
     "note_ru": "Крупные скругления, вкладки-пилюли, тональные поверхности без рамок", "note_en": "Big round corners, pill tabs, tonal surfaces without outlines"},
    {"id": "paper", "ru": "Лист", "en": "Sheet",
     "note_ru": "Документ лежит листом на столе: поля, тень, спокойная рамка окна", "note_en": "The document lies like a sheet on a desk: margins, a shadow, a calm frame"},
    {"id": "glass", "ru": "Стекло", "en": "Glass",
     "note_ru": "Полупрозрачные панели над мягким градиентом акцента", "note_en": "Translucent panels over a soft accent gradient"},
    {"id": "studio", "ru": "Студия", "en": "Studio",
     "note_ru": "Плотно, как в IDE: квадратные вкладки, цветная строка состояния", "note_en": "Dense like an IDE: square tabs, a coloured status bar"},
    {"id": "grid", "ru": "Сетка", "en": "Grid",
     "note_ru": "Прямые углы, линейки и моноширинные подписи", "note_en": "Square corners, rules and monospace captions"},
    {"id": "zen", "ru": "Дзен", "en": "Zen",
     "note_ru": "Только текст: панели проявляются при наведении", "note_en": "Just the text: panels appear on hover"},
    {"id": "brutal", "ru": "Брутализм", "en": "Brutal",
     "note_ru": "Жирные контуры, жёсткие тени со сдвигом, плоские цвета", "note_en": "Bold outlines, hard offset shadows, flat colours"},
]
DESIGN_IDS: Tuple[str, ...] = tuple(row["id"] for row in DESIGNS)
PALETTE_IDS: Tuple[str, ...] = tuple(row["id"] for row in PALETTES)
ACCENT_CHOICES: Tuple[str, ...] = ("auto", "palette", *ACCENT_IDS, "custom")

# MemuAI design -> the closest MemuNote design.
_FROM_MEMUAI = {"classic": "classic", "aurora": "soft", "focus": "zen", "grid": "grid", "glass": "glass",
                "soft": "soft", "terminal": "grid", "brutal": "brutal", "notebook": "paper", "messenger": "soft",
                "retro": "classic", "clay": "soft", "studio": "studio", "zen": "zen"}

def palette(palette_id: str) -> Optional[Dict[str, Any]]:
    return next((row for row in PALETTES if row["id"] == palette_id), None)

# ---- colour maths (WCAG relative luminance) -------------------------------------------------

def _rgb(hex_value: str) -> Tuple[float, float, float]:
    value = hex_value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]

def _hex(rgb: Tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02x}" for c in rgb)

def luminance(hex_value: str) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in _rgb(hex_value))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b

def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)

def _mix(a: str, b: str, t: float) -> str:
    ra, rb = _rgb(a), _rgb(b)
    return _hex(tuple(x + (y - x) * t for x, y in zip(ra, rb)))  # type: ignore[arg-type]

def fit_accent(accent: str, backgrounds: List[str], mode: str, minimum: float = 4.5) -> str:
    """Move ``accent`` towards white (dark mode) or black (light mode) until it reads on every background."""
    if not HEX_RE.match(accent or ""):
        return accent
    grounds = [b for b in backgrounds if HEX_RE.match(b or "")]
    if not grounds:
        return accent
    target = "#ffffff" if mode == "dark" else "#000000"
    value = accent
    for step in range(0, 21):
        value = _mix(accent, target, step * 0.05)
        if min(contrast(value, b) for b in grounds) >= minimum:
            return value
    return value

def on_accent(accent: str, preferred: str = "") -> str:
    """Text colour for a filled accent button: the preferred one when it reads, else black or white."""
    if not HEX_RE.match(accent or ""):
        return preferred
    if HEX_RE.match(preferred or "") and contrast(preferred, accent) >= 4.5:
        return preferred
    return "#111111" if contrast("#111111", accent) >= contrast("#ffffff", accent) else "#ffffff"

# ---- resolution -----------------------------------------------------------------------------

def normalize_palette(value: Any) -> str:
    return value if isinstance(value, str) and value in PALETTE_IDS else "auto"

def normalize_design(value: Any) -> str:
    return value if isinstance(value, str) and value in DESIGN_IDS else "auto"

def normalize_accent(value: Any) -> str:
    return value if isinstance(value, str) and value in ACCENT_CHOICES else "auto"

def normalize_hex(value: Any) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"#?[0-9a-fA-F]{3}", text):
        text = "#" + "".join(ch * 2 for ch in text.lstrip("#"))
    if re.fullmatch(r"[0-9a-fA-F]{6}", text):
        text = "#" + text
    return text.lower() if HEX_RE.match(text) else ""

def design_for(setting: str, memuai_design: str = "") -> str:
    setting = normalize_design(setting)
    if setting != "auto":
        return setting
    return _FROM_MEMUAI.get(str(memuai_design or ""), "classic")

def palette_tokens(mode: str, palette_id: str) -> Dict[str, str]:
    """Full token set of a palette (no accent) or {} when it cannot be resolved."""
    row = palette(palette_id)
    if not row or palette_id == "auto":
        return {}
    themes = _themes()
    if row.get(mode) is None:
        base: Dict[str, str] = {}
        if themes:
            try:
                base = dict(themes.variant_tokens(mode, ""))
            except Exception:  # noqa: BLE001
                base = {}
        base.update(OWN[palette_id][mode])
        return base
    if not themes:
        return {}
    try:
        return dict(themes.variant_tokens(mode, row[mode]))
    except Exception:  # noqa: BLE001
        return {}

def resolve(settings: Dict[str, Any], prefs: Dict[str, Any]) -> Dict[str, Any]:
    """Mode, tokens and design for MemuNote from its settings and MemuAI's preferences."""
    mode = settings.get("theme", "auto")
    if mode not in ("dark", "light"):
        mode = "light" if prefs.get("theme") == "light" else "dark"
    palette_id = normalize_palette(settings.get("palette"))
    accent_id = normalize_accent(settings.get("accent"))
    themes = _themes()
    memuai_accent = str(prefs.get("accent_color") or "")
    tokens: Dict[str, str] = {}
    if palette_id == "auto":
        if themes:
            try:
                tokens = dict(themes.resolve_tokens(mode, str(prefs.get(f"surface_{mode}") or ""), memuai_accent))
            except Exception:  # noqa: BLE001
                tokens = {}
    else:
        tokens = palette_tokens(mode, palette_id)
    table = accents()
    accent_value = ""
    if accent_id == "custom":
        accent_value = normalize_hex(settings.get("accent_custom"))
    elif accent_id == "palette":
        row = palette(palette_id) or {}
        accent_value = (row.get("accent") or {}).get(mode, "")
    elif accent_id in table:
        accent_value = table[accent_id][mode]
    if not accent_value and tokens:
        # auto (or an unusable custom value): MemuAI's accent.
        key = memuai_accent if memuai_accent in table else next(iter(table))
        accent_value = table[key][mode]
    if accent_value and tokens:
        fitted = fit_accent(accent_value, [tokens.get("--bg", ""), tokens.get("--panel", "")], mode)
        tokens["--accent"] = fitted
        tokens["--accent-soft"] = f"color-mix(in srgb, {fitted} {12 if mode == 'dark' else 10}%, transparent)"
        tokens["--on-accent"] = on_accent(fitted, tokens.get("--on-accent", ""))
    return {"mode": mode, "tokens": tokens, "palette": palette_id, "accent": accent_id,
            "design": design_for(settings.get("design", "auto"), str(prefs.get("ui_design") or "")),
            "design_setting": normalize_design(settings.get("design"))}

def swatch(mode: str, palette_id: str, prefs: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """Four colours for a palette card: background, panel, text and accent."""
    tokens = palette_tokens(mode, palette_id) if palette_id != "auto" else {}
    if palette_id == "auto" and prefs is not None:
        themes = _themes()
        if themes:
            try:
                tokens = dict(themes.resolve_tokens(mode, str(prefs.get(f"surface_{mode}") or ""), str(prefs.get("accent_color") or "")))
            except Exception:  # noqa: BLE001
                tokens = {}
    row = palette(palette_id) or {}
    accent = (row.get("accent") or {}).get(mode) or tokens.get("--accent", "")
    return {"bg": tokens.get("--bg", ""), "panel": tokens.get("--panel", ""), "text": tokens.get("--text", ""),
            "muted": tokens.get("--muted", ""), "accent": accent, "kw": tokens.get("--tok-kw", ""),
            "str": tokens.get("--tok-str", ""), "fn": tokens.get("--tok-fn", "")}

def catalog(lang: str = "ru", prefs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Everything the in-app appearance picker needs, in one call."""
    en = lang == "en"
    table = accents()
    return {
        "palettes": [{"id": row["id"], "label": row["en" if en else "ru"],
                      "dark": swatch("dark", row["id"], prefs or {}), "light": swatch("light", row["id"], prefs or {})}
                     for row in PALETTES],
        "accents": [{"id": key, "label": row["en" if en else "ru"], "dark": row["dark"], "light": row["light"]}
                    for key, row in table.items()],
        "designs": [{"id": row["id"], "label": row["en" if en else "ru"], "note": row["note_en" if en else "note_ru"]}
                    for row in DESIGNS],
    }
