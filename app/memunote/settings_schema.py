"""MemuNote settings schema — the single description of every preference (Round 38).

Used by MemuNote itself (``settings.py`` derives its defaults, choices and ranges from it) and by
MemuAI's Settings → «MemuNote» page (``app/app_settings_api.py`` serves it and validates writes with
:func:`validate`). Each entry: key, type (bool | int | float | enum | font | text | url), default,
group, ru/en label and description, and per type ``min``/``max``/``step``/``unit`` or ``choices``.
``hidden`` entries are internal (kept in the file, never shown). Stdlib only: MemuNote ships alone.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

FONT_PRESETS = ["JetBrains Mono", "Cascadia Code", "Consolas", "Fira Code", "Source Code Pro", "Courier New",
                "Segoe UI", "Inter", "Georgia", "Times New Roman", "Arial", "Calibri"]

ENCODING_CHOICES = [("utf-8", "UTF-8"), ("utf-8-sig", "UTF-8 BOM"), ("utf-16-le", "UTF-16 LE"), ("utf-16-be", "UTF-16 BE"),
                    ("cp1251", "Windows-1251"), ("koi8-r", "KOI8-R"), ("cp866", "CP866 (DOS)"), ("cp1252", "Windows-1252"),
                    ("iso-8859-1", "ISO-8859-1"), ("iso-8859-5", "ISO-8859-5"), ("mac-cyrillic", "Mac Cyrillic"),
                    ("cp1250", "Windows-1250"), ("cp1253", "Windows-1253"), ("cp1254", "Windows-1254"),
                    ("gb18030", "GB18030"), ("big5", "Big5"), ("shift_jis", "Shift-JIS"), ("euc-kr", "EUC-KR")]

GROUPS: List[Dict[str, str]] = [
    {"id": "appearance", "ru": "Внешний вид", "en": "Appearance"},
    {"id": "editor", "ru": "Редактор", "en": "Editor"},
    {"id": "files", "ru": "Файлы и сохранение", "en": "Files and saving"},
    {"id": "ai", "ru": "ИИ", "en": "AI"},
    {"id": "hotkeys", "ru": "Горячие клавиши", "en": "Shortcuts"},
    {"id": "advanced", "ru": "Дополнительно", "en": "Advanced"},
]


def _b(key, default, group, ru, en, dru="", den="", **extra):
    return {"key": key, "type": "bool", "default": default, "group": group, "ru": ru, "en": en, "desc_ru": dru, "desc_en": den, **extra}


def _i(key, default, group, ru, en, lo, hi, step=1, unit="", dru="", den="", kind="int", **extra):
    return {"key": key, "type": kind, "default": default, "group": group, "ru": ru, "en": en, "desc_ru": dru, "desc_en": den,
            "min": lo, "max": hi, "step": step, "unit": unit, **extra}


def _e(key, default, group, ru, en, choices, dru="", den="", **extra):
    """``choices``: [(value, ru, en)]."""
    return {"key": key, "type": "enum", "default": default, "group": group, "ru": ru, "en": en, "desc_ru": dru, "desc_en": den,
            "choices": [{"value": v, "ru": r, "en": e} for v, r, e in choices], **extra}


_AUTO_LANG = [("auto", "Как в MemuAI", "Same as MemuAI"), ("ru", "Русский", "Русский"), ("en", "English", "English")]
_AUTO_THEME = [("auto", "Как в MemuAI", "Same as MemuAI"), ("dark", "Тёмная", "Dark"), ("light", "Светлая", "Light")]

SCHEMA: List[Dict[str, Any]] = [
    # ---------------------------------------------------------------- appearance
    _e("language", "auto", "appearance", "Язык", "Language", _AUTO_LANG),
    _e("theme", "auto", "appearance", "Тема", "Theme", _AUTO_THEME, "«Как в MemuAI» берёт тему, фон и акцент MemuAI.", "“Same as MemuAI” uses MemuAI's theme, surface and accent."),
    {"key": "font_family", "type": "font", "default": "JetBrains Mono", "group": "appearance", "ru": "Шрифт редактора", "en": "Editor font",
     "desc_ru": "Любой установленный шрифт; список — популярные.", "desc_en": "Any installed font; the list has popular ones.", "suggest": FONT_PRESETS, "maxlen": 64},
    _i("font_size", 14, "appearance", "Размер шрифта", "Font size", 8, 40, 1, " px"),
    _i("line_height", 1.55, "appearance", "Межстрочный интервал", "Line height", 1.0, 2.6, 0.05, "", kind="float"),
    _i("zoom", 100, "appearance", "Масштаб", "Zoom", 50, 300, 10, "%", "Также Ctrl+= / Ctrl+− в окне.", "Also Ctrl+= / Ctrl+− in the window."),
    _b("show_toolbar", True, "appearance", "Панель инструментов", "Toolbar"),
    _b("show_statusbar", True, "appearance", "Строка состояния", "Status bar"),
    _b("show_sidebar", False, "appearance", "Боковая панель при запуске", "Sidebar on start", "Структура документа и файлы (Ctrl+B).", "Outline and files (Ctrl+B)."),
    _b("compact_tabs", False, "appearance", "Компактные вкладки", "Compact tabs"),
    _b("smooth_caret", True, "appearance", "Плавный курсор", "Smooth caret"),
    # ---------------------------------------------------------------- editor
    _b("word_wrap", True, "editor", "Перенос строк", "Word wrap", "Alt+Z в окне.", "Alt+Z in the window."),
    _b("line_numbers", True, "editor", "Номера строк", "Line numbers"),
    _b("minimap", False, "editor", "Мини-карта", "Minimap"),
    _b("highlight_active_line", True, "editor", "Подсветка текущей строки", "Highlight the current line"),
    _b("show_whitespace", False, "editor", "Показывать пробелы и табы", "Show spaces and tabs"),
    _b("indent_with_tabs", False, "editor", "Отступ табуляцией", "Indent with tabs"),
    _i("tab_size", 4, "editor", "Размер табуляции", "Tab size", 1, 8),
    _b("auto_close_brackets", True, "editor", "Закрывать скобки и кавычки", "Auto-close brackets and quotes"),
    _b("bracket_matching", True, "editor", "Подсветка парных скобок", "Bracket matching"),
    _b("fold_gutter", True, "editor", "Сворачивание блоков", "Code folding"),
    _b("scroll_past_end", True, "editor", "Прокрутка за конец файла", "Scroll past the end"),
    _b("highlight_selection_matches", True, "editor", "Подсветка совпадений выделения", "Highlight selection matches"),
    _b("spell_check", True, "editor", "Проверка орфографии", "Spell check", "Встроенный словарь RU/EN, без интернета.", "Built-in RU/EN dictionary, offline."),
    _b("code_autocomplete", True, "editor", "Автодополнение в коде", "Code completion", "Подсказки слов и ключевых слов в файлах кода.", "Word and keyword suggestions in code files."),
    _e("markdown_preview", "live", "editor", "Режим Markdown", "Markdown mode",
       [("live", "Живой редактор", "Live editor"), ("split", "Текст и просмотр рядом", "Source and preview"),
        ("off", "Только текст", "Source only"), ("preview", "Только просмотр", "Preview only")]),
    # ---------------------------------------------------------------- files
    _e("autosave", "off", "files", "Автосохранение", "Autosave",
       [("off", "Выключено", "Off"), ("focus", "При уходе из окна", "When the window loses focus"), ("delay", "Через паузу", "After a pause")]),
    _i("autosave_delay", 2000, "files", "Пауза автосохранения", "Autosave delay", 500, 60000, 500, " ms", when="autosave=delay"),
    _b("restore_session", True, "files", "Восстанавливать вкладки", "Restore tabs", "Открывать прошлые вкладки при запуске.", "Reopen the last tabs on start."),
    _b("recovery", True, "files", "Восстановление несохранённого", "Unsaved work recovery", "Черновики переживают сбой и закрытие окна.", "Drafts survive a crash or a closed window."),
    _e("default_encoding", "utf-8", "files", "Кодировка новых файлов", "Encoding of new files", [(v, l, l) for v, l in ENCODING_CHOICES]),
    _e("default_eol", "crlf" if os.name == "nt" else "lf", "files", "Конец строки", "Line ending",
       [("crlf", "CRLF (Windows)", "CRLF (Windows)"), ("lf", "LF (Unix)", "LF (Unix)"), ("cr", "CR", "CR")]),
    _b("trim_trailing_whitespace", False, "files", "Убирать пробелы в конце строк", "Trim trailing spaces"),
    _b("final_newline", False, "files", "Перевод строки в конце файла", "Final newline"),
    _b("backup_on_save", False, "files", "Резервная копия при сохранении", "Backup on save", "Рядом с файлом появляется «.bak».", "A “.bak” file is kept next to it."),
    _b("detect_external_changes", True, "files", "Следить за изменениями на диске", "Watch for changes on disk"),
    _b("ask_markdown_convert", True, "files", "Предлагать Markdown для разметки", "Offer Markdown for formatting"),
    _b("rich_save_warning", True, "files", "Предупреждать о потере оформления", "Warn about losing formatting", "При сохранении документа Word как текст.", "When saving a Word document as text."),
    _b("pdf_page_numbers", True, "files", "Номера страниц в PDF", "Page numbers in PDF"),
    # ---------------------------------------------------------------- AI
    _b("ai_enabled", True, "ai", "ИИ в MemuNote", "AI in MemuNote", "Панель ИИ, команды и правки. Работает через MemuAI Proxy.", "AI panel, commands and edits. Works through MemuAI Proxy."),
    {"key": "ai_model", "type": "text", "default": "", "group": "ai", "ru": "Модель", "en": "Model", "maxlen": 120, "models": True,
     "desc_ru": "Пусто — модель по умолчанию в Proxy.", "desc_en": "Empty — the Proxy's default model.", "when": "ai_enabled"},
    _b("ai_web_search", False, "ai", "Поиск в интернете", "Web search", when="ai_enabled"),
    _b("ai_memory", True, "ai", "Память MemuAI", "MemuAI memory", "ИИ учитывает то, что MemuAI знает о вас.", "AI uses what MemuAI knows about you.", when="ai_enabled"),
    _b("ai_memory_write", True, "ai", "Запоминать новое", "Remember new facts", when="ai_enabled"),
    _b("ai_autocomplete", False, "ai", "ИИ-дописывание", "AI autocomplete", "Серая подсказка продолжения, Tab — принять.", "A grey continuation hint, Tab accepts.", when="ai_enabled"),
    _i("ai_autocomplete_delay", 900, "ai", "Задержка дописывания", "Autocomplete delay", 300, 5000, 100, " ms", when="ai_autocomplete"),
    _i("ai_context_chars", 60000, "ai", "Контекст документа", "Document context", 2000, 400000, 1000, "",
       "Сколько символов документа видит ИИ.", "How many characters of the document the AI sees.", when="ai_enabled"),
    {"key": "ai_translate_to", "type": "text", "default": "", "group": "ai", "ru": "Язык перевода", "en": "Translate to", "maxlen": 40,
     "desc_ru": "Пусто — спрашивать каждый раз.", "desc_en": "Empty — ask every time.", "placeholder": "English", "when": "ai_enabled"},
    {"key": "proxy_url", "type": "url", "default": "", "group": "ai", "ru": "Адрес Proxy", "en": "Proxy address", "maxlen": 200,
     "desc_ru": "Только локальный адрес. Пусто — найти автоматически.", "desc_en": "Local address only. Empty — find it automatically.",
     "placeholder": "http://127.0.0.1:8317", "local_only": True},
    # ---------------------------------------------------------------- advanced
    _i("large_file_mb", 16, "advanced", "Большой файл, МБ", "Large file, MB", 1, 512, 1, " MB",
       "Файлы крупнее открываются в быстром режиме просмотра.", "Bigger files open in the fast viewer mode."),
    _b("spellcheck", False, "advanced", "Системная проверка орфографии", "System spell check", hidden=True),
    _b("md_live_migrated", False, "advanced", "", "", hidden=True),
]

# Read-only reference shown in MemuAI (MemuNote's shortcuts are fixed; F1 in the window lists them too).
SHORTCUTS: List[Tuple[str, str, str]] = [
    ("Ctrl+N", "Новая вкладка", "New tab"), ("Ctrl+O", "Открыть файл", "Open file"), ("Ctrl+S", "Сохранить", "Save"),
    ("Ctrl+Shift+S", "Сохранить как", "Save as"), ("Ctrl+Alt+S", "Сохранить все", "Save all"), ("Ctrl+W", "Закрыть вкладку", "Close tab"),
    ("Ctrl+Shift+T", "Вернуть закрытую вкладку", "Reopen closed tab"), ("Ctrl+F / Ctrl+H", "Найти / заменить", "Find / replace"),
    ("Ctrl+G", "Перейти к строке", "Go to line"), ("Ctrl+D", "Следующее совпадение", "Select next match"),
    ("Alt+Z", "Перенос строк", "Word wrap"), ("Ctrl+\\", "Разделить окно", "Split view"), ("Ctrl+B", "Жирный / боковая панель", "Bold / sidebar"),
    ("Ctrl+Shift+V", "Просмотр Markdown", "Markdown preview"), ("Ctrl+J", "Команда ИИ", "AI command"), ("Ctrl+Shift+L", "Панель ИИ", "AI panel"),
    ("F5", "Запустить файл", "Run file"), ("Ctrl+P", "Быстрое открытие", "Quick open"), ("Ctrl+Shift+P", "Палитра команд", "Command palette"),
    ("Ctrl+= / Ctrl+−", "Масштаб", "Zoom"), ("F11", "Во весь экран", "Full screen"), ("Ctrl+,", "Настройки", "Settings"), ("F1", "Все сочетания", "All shortcuts"),
]

BY_KEY: Dict[str, Dict[str, Any]] = {item["key"]: item for item in SCHEMA}
DEFAULTS: Dict[str, Any] = {item["key"]: item["default"] for item in SCHEMA}
CHOICES: Dict[str, Tuple[Any, ...]] = {item["key"]: tuple(c["value"] for c in item["choices"]) for item in SCHEMA if item["type"] == "enum"}
RANGES: Dict[str, Tuple[Any, Any]] = {item["key"]: (item["min"], item["max"]) for item in SCHEMA if item["type"] in ("int", "float")}


def _local_url(value: str) -> bool:
    import urllib.parse
    try:
        parts = urllib.parse.urlsplit(value)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and (parts.hostname or "") in ("127.0.0.1", "localhost", "::1")


def check(key: str, value: Any) -> Tuple[bool, Any, str]:
    """Strict check of one value: ``(ok, clean value, error code)``. Used for writes from MemuAI."""
    item = BY_KEY.get(key)
    if item is None or item.get("hidden"):
        return False, None, "unknown_key"
    kind = item["type"]
    if kind == "bool":
        return (True, value, "") if isinstance(value, bool) else (False, None, "type")
    if kind in ("int", "float"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False, None, "type"
        if kind == "int" and float(value) != int(value):
            return False, None, "type"
        if not item["min"] <= value <= item["max"]:
            return False, None, "range"
        return True, (int(value) if kind == "int" else round(float(value), 2)), ""
    if kind == "enum":
        return (True, value, "") if value in CHOICES[key] else (False, None, "choice")
    if not isinstance(value, str):
        return False, None, "type"
    value = value.strip()
    if len(value) > item.get("maxlen", 200):
        return False, None, "length"
    if kind == "font" and (not value or any(ch in value for ch in ";{}<>\"\\")):
        return False, None, "font"
    if kind == "url" and value and (not _local_url(value) if item.get("local_only") else not value.startswith(("http://", "https://"))):
        return False, None, "url"
    return True, value, ""


def validate(changes: Any) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """``(clean changes, {key: error})`` — nothing is written when errors is not empty."""
    clean: Dict[str, Any] = {}
    errors: Dict[str, str] = {}
    for key, value in (changes.items() if isinstance(changes, dict) else []):
        ok, val, err = check(str(key), value)
        if ok:
            clean[key] = val
        else:
            errors[str(key)] = err
    return clean, errors


def public() -> Dict[str, Any]:
    """The schema as JSON for the MemuAI page (hidden keys left out)."""
    return {"groups": GROUPS, "items": [item for item in SCHEMA if not item.get("hidden")],
            "shortcuts": [{"keys": k, "ru": r, "en": e} for k, r, e in SHORTCUTS]}
