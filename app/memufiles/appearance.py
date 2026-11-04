"""MemuFiles appearance: palettes, accents, designs and folder colours (Settings → Appearance).

The model is MemuNote's (``app/memunote/appearance.py``): everything follows MemuAI by default
(``"auto"``) — the light/dark mode, the surface, the accent and the design — and each of them can be
chosen for MemuFiles alone:

* ``palette``      — one of MemuNote's palettes (MemuAI's own variants plus GitHub, Gruvbox, Monokai,
  One, Tokyo Night and Sepia), each with a dark and a light face;
* ``accent``       — ``auto`` (MemuAI's accent), ``palette`` (the palette's own colour), one of MemuAI's
  named accents or ``custom`` with ``accent_custom`` = ``#rrggbb``. A chosen accent is fitted to the
  background so selections, links and active items stay readable;
* ``design``       — the shape language of the window (``static/designs.css``): tabs, corners,
  outlines, panels. ``auto`` picks the closest match to MemuAI's design;
* ``folder_color`` — the colour of folder icons.

Stdlib only. The palettes and the colour maths live in MemuNote's module, which ships with MemuFiles;
every use is guarded, so a missing module only drops back to MemuAI's colours.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _note():
    try:
        from ..memunote import appearance
        return appearance
    except Exception:  # noqa: BLE001 - standalone/dev
        return None


def _themes():
    try:
        from .. import themes
        return themes
    except Exception:  # noqa: BLE001
        return None


_N = _note()

PALETTES: List[Dict[str, Any]] = list(_N.PALETTES) if _N else [{"id": "auto", "ru": "Как в MemuAI", "en": "Same as MemuAI"}]
PALETTE_IDS: Tuple[str, ...] = tuple(row["id"] for row in PALETTES)

# Labels for the schema; the colours themselves come from MemuAI (``themes.ACCENTS``) at runtime.
_ACCENT_LABELS: Dict[str, Dict[str, str]] = dict(getattr(_N, "_FALLBACK_ACCENTS", {}) or {}) if _N else {}
ACCENT_IDS: Tuple[str, ...] = tuple(_ACCENT_LABELS)
ACCENT_CHOICES: Tuple[str, ...] = ("auto", "palette", *ACCENT_IDS, "custom")

# The accents MemuFiles had before it took MemuAI's set: the closest named colour.
LEGACY_ACCENTS: Dict[str, str] = {"pink": "rose", "red": "crimson", "orange": "amber", "green": "forest"}

DESIGNS: List[Dict[str, str]] = [
    {"id": "auto", "ru": "Как в MemuAI", "en": "Same as MemuAI",
     "note_ru": "Подбирает похожий дизайн к выбранному в MemuAI", "note_en": "Picks the closest match to MemuAI's design"},
    {"id": "classic", "ru": "Классический", "en": "Classic",
     "note_ru": "Вкладки-ушки и тонкие рамки, как в Проводнике", "note_en": "Ear tabs and hairlines, like Explorer"},
    {"id": "soft", "ru": "Мягкий", "en": "Soft",
     "note_ru": "Панели-острова, крупные скругления, вкладки-пилюли без рамок", "note_en": "Island panels, big round corners, pill tabs without outlines"},
    {"id": "paper", "ru": "Лист", "en": "Sheet",
     "note_ru": "Папка лежит листом на столе: поля и мягкая тень", "note_en": "The folder lies like a sheet on a desk: margins and a soft shadow"},
    {"id": "glass", "ru": "Стекло", "en": "Glass",
     "note_ru": "Полупрозрачные панели над мягким градиентом акцента", "note_en": "Translucent panels over a soft accent gradient"},
    {"id": "studio", "ru": "Студия", "en": "Studio",
     "note_ru": "Плотно, как в IDE: квадратные вкладки, цветная строка состояния", "note_en": "Dense like an IDE: square tabs, a coloured status bar"},
    {"id": "grid", "ru": "Сетка", "en": "Grid",
     "note_ru": "Прямые углы, линейки между строками, моноширинные подписи", "note_en": "Square corners, rules between rows, monospace captions"},
    {"id": "zen", "ru": "Дзен", "en": "Zen",
     "note_ru": "Только файлы: панели проявляются при наведении", "note_en": "Just the files: the bars appear on hover"},
    {"id": "brutal", "ru": "Брутализм", "en": "Brutal",
     "note_ru": "Жирные контуры, жёсткие тени со сдвигом, плоские цвета", "note_en": "Bold outlines, hard offset shadows, flat colours"},
]
DESIGN_IDS: Tuple[str, ...] = tuple(row["id"] for row in DESIGNS)

# MemuAI design -> the closest MemuFiles design.
_FROM_MEMUAI = {"classic": "classic", "material": "classic", "aurora": "soft", "focus": "zen", "grid": "grid",
                "glass": "glass", "soft": "soft", "terminal": "grid", "brutal": "brutal", "notebook": "paper",
                "messenger": "soft", "retro": "classic", "clay": "soft", "studio": "studio", "zen": "zen"}

FOLDER_COLORS: List[Dict[str, str]] = [
    {"id": "classic", "ru": "Жёлтые, как в Windows", "en": "Yellow, like Windows", "dark": "#e8b65a", "light": "#e5a93f"},
    {"id": "blue", "ru": "Синие", "en": "Blue", "dark": "#62a4ee", "light": "#3f86dd"},
    {"id": "accent", "ru": "Цветом акцента", "en": "Accent colour", "dark": "", "light": ""},
    {"id": "mono", "ru": "Нейтральные", "en": "Neutral", "dark": "#a3a9b5", "light": "#8a919e"},
]
FOLDER_IDS: Tuple[str, ...] = tuple(row["id"] for row in FOLDER_COLORS)


def normalize_palette(value: Any) -> str:
    return value if isinstance(value, str) and value in PALETTE_IDS else "auto"


def normalize_design(value: Any) -> str:
    return value if isinstance(value, str) and value in DESIGN_IDS else "auto"


def normalize_accent(value: Any) -> str:
    if isinstance(value, str) and value in LEGACY_ACCENTS:
        value = LEGACY_ACCENTS[value]
    return value if isinstance(value, str) and value in ACCENT_CHOICES else "auto"


def normalize_folders(value: Any) -> str:
    return value if isinstance(value, str) and value in FOLDER_IDS else "classic"


def normalize_hex(value: Any) -> str:
    if _N:
        return _N.normalize_hex(value)
    return ""


def design_for(setting: Any, memuai_design: str = "") -> str:
    setting = normalize_design(setting)
    if setting != "auto":
        return setting
    return _FROM_MEMUAI.get(str(memuai_design or ""), "classic")


def _memuai_tokens(mode: str, prefs: Dict[str, Any]) -> Dict[str, str]:
    themes = _themes()
    if not themes:
        return {}
    try:
        return dict(themes.resolve_tokens(mode, str(prefs.get(f"surface_{mode}") or ""), str(prefs.get("accent_color") or "")))
    except Exception:  # noqa: BLE001
        return {}


def resolve(settings: Dict[str, Any], prefs: Dict[str, Any]) -> Dict[str, Any]:
    """Mode, tokens, design and folder colour for MemuFiles from its settings and MemuAI's preferences.

    With the palette and the accent both on ``auto`` the tokens are exactly MemuAI's (its accent, a
    custom MemuAI theme included); anything chosen here is resolved and fitted to the background.
    """
    mode = settings.get("theme", "auto")
    if mode not in ("dark", "light"):
        mode = "light" if prefs.get("theme") == "light" else "dark"
    palette_id = normalize_palette(settings.get("palette"))
    accent_id = normalize_accent(settings.get("accent"))
    if palette_id == "auto" or not _N:
        tokens = _memuai_tokens(mode, prefs)
    else:
        tokens = _N.palette_tokens(mode, palette_id) or _memuai_tokens(mode, prefs)
    if _N and tokens:
        table = _N.accents()
        accent_value = ""
        if accent_id == "custom":
            accent_value = _N.normalize_hex(settings.get("accent_custom"))
        elif accent_id == "palette":
            accent_value = ((_N.palette(palette_id) or {}).get("accent") or {}).get(mode, "")
        elif accent_id in table:
            accent_value = table[accent_id][mode]
        if not accent_value and palette_id != "auto":
            # MemuAI's accent on a palette chosen here (also an unusable custom value).
            memuai_accent = str(prefs.get("accent_color") or "")
            key = memuai_accent if memuai_accent in table else next(iter(table))
            accent_value = table[key][mode]
        if accent_value:
            fitted = _N.fit_accent(accent_value, [tokens.get("--bg", ""), tokens.get("--panel", "")], mode)
            tokens["--accent"] = fitted
            tokens["--accent-soft"] = f"color-mix(in srgb, {fitted} {12 if mode == 'dark' else 10}%, transparent)"
            tokens["--on-accent"] = _N.on_accent(fitted, tokens.get("--on-accent", ""))
    return {"mode": mode, "tokens": tokens, "palette": palette_id, "accent": accent_id,
            "design": design_for(settings.get("design"), str(prefs.get("ui_design") or "")),
            "design_setting": normalize_design(settings.get("design")),
            "folders": normalize_folders(settings.get("folder_color"))}


def _swatch(mode: str, palette_id: str, prefs: Dict[str, Any]) -> Dict[str, str]:
    if not _N:
        return {}
    if palette_id == "auto":
        tokens = _memuai_tokens(mode, prefs)
    else:
        tokens = _N.palette_tokens(mode, palette_id)
    row = _N.palette(palette_id) or {}
    accent = (row.get("accent") or {}).get(mode) or tokens.get("--accent", "")
    return {"bg": tokens.get("--bg", ""), "panel": tokens.get("--panel", ""), "deep": tokens.get("--bg-deep", ""),
            "text": tokens.get("--text", ""), "muted": tokens.get("--muted", ""), "border": tokens.get("--border", ""),
            "accent": accent}


def catalog(lang: str = "ru", prefs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Everything the in-app appearance picker needs, in one call."""
    en = lang == "en"
    prefs = prefs or {}
    table = _N.accents() if _N else {}
    return {
        "palettes": [{"id": row["id"], "label": row["en" if en else "ru"],
                      "dark": _swatch("dark", row["id"], prefs), "light": _swatch("light", row["id"], prefs)}
                     for row in PALETTES],
        "accents": [{"id": key, "label": (_ACCENT_LABELS.get(key) or row).get("en" if en else "ru", key),
                     "dark": row["dark"], "light": row["light"]}
                    for key, row in table.items() if key in ACCENT_IDS],
        "designs": [{"id": row["id"], "label": row["en" if en else "ru"], "note": row["note_en" if en else "note_ru"]}
                    for row in DESIGNS],
        "folders": [{"id": row["id"], "label": row["en" if en else "ru"], "dark": row["dark"], "light": row["light"]}
                    for row in FOLDER_COLORS],
        "memuai_design": str(prefs.get("ui_design") or ""),
    }
