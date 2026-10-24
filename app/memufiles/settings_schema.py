"""MemuFiles settings schema — the single description of every preference (Round 38).

Used by MemuFiles itself (``settings.py`` derives its defaults and load/save checks from it) and by
MemuAI's Settings → «MemuFiles» page (``app/app_settings_api.py`` serves it and validates writes with
:func:`validate`). Same shape as ``app/memunote/settings_schema.py``: key, type (bool | int | enum |
text | url), default, group, ru/en label and description, ``min``/``max``/``step``/``unit`` or
``choices``. ``hidden`` entries are app state kept in the same file (pins, per-folder views) and are
never shown or written from MemuAI. Stdlib only: MemuFiles ships alone.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

GROUPS: List[Dict[str, str]] = [
    {"id": "appearance", "ru": "Внешний вид", "en": "Appearance"},
    {"id": "view", "ru": "Просмотр", "en": "Browsing"},
    {"id": "files", "ru": "Файлы", "en": "Files"},
    {"id": "tabs", "ru": "Запуск и вкладки", "en": "Start-up and tabs"},          # r41-files
    {"id": "archives", "ru": "Архивы", "en": "Archives"},                         # r41-files
    {"id": "ai", "ru": "ИИ", "en": "AI"},
    {"id": "hotkeys", "ru": "Горячие клавиши", "en": "Shortcuts"},
    {"id": "windows", "ru": "Интеграция с Windows", "en": "Windows integration"},
    {"id": "advanced", "ru": "Дополнительно", "en": "Advanced"},
]


def _b(key, default, group, ru, en, dru="", den="", **extra):
    return {"key": key, "type": "bool", "default": default, "group": group, "ru": ru, "en": en, "desc_ru": dru, "desc_en": den, **extra}


def _i(key, default, group, ru, en, lo, hi, step=1, unit="", dru="", den="", **extra):
    return {"key": key, "type": "int", "default": default, "group": group, "ru": ru, "en": en, "desc_ru": dru, "desc_en": den,
            "min": lo, "max": hi, "step": step, "unit": unit, **extra}


def _e(key, default, group, ru, en, choices, dru="", den="", **extra):
    """``choices``: [(value, ru, en)]."""
    return {"key": key, "type": "enum", "default": default, "group": group, "ru": ru, "en": en, "desc_ru": dru, "desc_en": den,
            "choices": [{"value": v, "ru": r, "en": e} for v, r, e in choices], **extra}


SCHEMA: List[Dict[str, Any]] = [
    # ---------------------------------------------------------------- appearance
    _e("language", "auto", "appearance", "Язык", "Language",
       [("auto", "Как в MemuAI", "Same as MemuAI"), ("ru", "Русский", "Русский"), ("en", "English", "English")]),
    _e("theme", "auto", "appearance", "Тема", "Theme",
       [("auto", "Как в MemuAI", "Same as MemuAI"), ("dark", "Тёмная", "Dark"), ("light", "Светлая", "Light")],
       "«Как в MemuAI» берёт тему, фон и акцент MemuAI.", "“Same as MemuAI” uses MemuAI's theme, surface and accent."),
    _e("density", "normal", "appearance", "Плотность", "Density",
       [("compact", "Компактно", "Compact"), ("normal", "Обычно", "Normal"), ("comfortable", "Просторно", "Comfortable")]),
    # r41-files: more ways to make MemuFiles your own (all applied at once, no restart)
    _e("accent", "auto", "appearance", "Акцентный цвет", "Accent colour",
       [("auto", "Как в теме", "From the theme"), ("blue", "Синий", "Blue"), ("violet", "Фиолетовый", "Violet"),
        ("pink", "Розовый", "Pink"), ("red", "Красный", "Red"), ("orange", "Оранжевый", "Orange"),
        ("green", "Зелёный", "Green"), ("teal", "Бирюзовый", "Teal"), ("graphite", "Графит", "Graphite")]),
    _e("ui_scale", "auto", "appearance", "Размер текста", "Text size",
       [("auto", "Как в MemuAI", "Same as MemuAI"), ("90", "Мельче (90 %)", "Smaller (90%)"), ("100", "Обычный (100 %)", "Normal (100%)"),
        ("110", "Крупнее (110 %)", "Larger (110%)"), ("125", "Крупный (125 %)", "Large (125%)")]),
    _e("font", "auto", "appearance", "Шрифт", "Font",
       [("auto", "Как в MemuAI", "Same as MemuAI"), ("segoe-variable", "Segoe UI Variable", "Segoe UI Variable"),
        ("segoe", "Segoe UI", "Segoe UI"), ("system", "Системный", "System"), ("rounded", "Округлый", "Rounded")]),
    _e("tile_size", "m", "appearance", "Размер плиток", "Tile size",
       [("s", "Маленькие", "Small"), ("m", "Средние", "Medium"), ("l", "Крупные", "Large")], "Вид «Плитка».", "The Tiles view."),
    _b("row_lines", False, "appearance", "Чередовать фон строк", "Alternate row shading", "Вид «Таблица».", "The Details view."),
    _e("selection_style", "fill", "appearance", "Выделение", "Selection",
       [("fill", "Заливка", "Fill"), ("outline", "Рамка", "Outline")]),
    _e("group_style", "accent", "appearance", "Заголовки групп", "Group headers",
       [("accent", "Цветом акцента", "Accent colour"), ("plain", "Спокойные", "Muted")]),
    _e("motion", "auto", "appearance", "Анимации", "Animations",
       [("auto", "Как в системе", "Follow the system"), ("on", "Включены", "On"), ("off", "Выключены", "Off")]),
    _b("custom_titlebar", True, "appearance", "Вкладки в заголовке окна", "Tabs in the window caption",
       "Своя полоса заголовка Windows; выключено — системная рамка.", "A custom Windows caption; off — the system frame."),
    _i("sidebar_width", 236, "appearance", "Ширина боковой панели", "Sidebar width", 160, 760, 4, " px"),
    _i("preview_width", 340, "appearance", "Ширина предпросмотра", "Preview width", 160, 760, 4, " px"),
    # ---------------------------------------------------------------- view
    _e("default_view", "details", "view", "Вид по умолчанию", "Default view",
       [("details", "Таблица", "Details"), ("list", "Список", "List"), ("tiles", "Плитка", "Tiles"), ("grid", "Значки", "Icons"), ("gallery", "Галерея", "Gallery")],
       "Для папок, где вид не меняли.", "For folders whose view you haven't changed."),
    _e("default_sort", "name", "view", "Сортировка по умолчанию", "Default sort",
       [("name", "По имени (А→Я)", "By name (A→Z)"), ("m", "Сначала новые", "Newest first"), ("type", "По типу", "By type"),
        ("s", "Сначала большие", "Largest first")],
       "Для папок, где её не меняли. «Загрузки» — всегда сначала новые, с группами по дате, как в Проводнике.",
       "For folders where you haven't changed it. Downloads always opens newest first, grouped by date, like Explorer."),
    _e("default_group", "none", "view", "Группировка по умолчанию", "Default grouping",
       [("none", "Без группировки", "None"), ("date", "По дате изменения", "By date modified"), ("type", "По типу", "By type"),
        ("size", "По размеру", "By size"), ("name", "По первой букве", "By first letter")]),
    _e("folders_order", "auto", "view", "Папки и файлы", "Folders and files",
       [("auto", "Как в Проводнике", "Like Explorer"), ("top", "Папки всегда сверху", "Folders always on top"),
        ("mixed", "Вперемешку", "Mixed together")],
       "Как в Проводнике: по имени — папки сверху, по дате (новые сначала) — файлы и папки идут вместе внутри групп.",
       "Like Explorer: by name — folders on top; newest first — files and folders go together inside the date groups."),
    _i("icon_size", 96, "view", "Размер значков", "Icon size", 32, 256, 8, " px", "Для видов «Значки» и «Галерея».", "For the Icons and Gallery views."),
    _b("show_hidden", False, "view", "Скрытые файлы", "Hidden files", "Ctrl+H в окне.", "Ctrl+H in the window."),
    _b("show_system", False, "view", "Защищённые системные файлы", "Protected system files",
       "Скрытые файлы Windows с атрибутом «системный» (desktop.ini, $RECYCLE.BIN…).",
       "Hidden Windows files with the System attribute (desktop.ini, $RECYCLE.BIN…).", when="show_hidden"),
    _b("show_extensions", True, "view", "Расширения файлов", "File extensions"),
    _b("single_click", False, "view", "Открывать одним щелчком", "Open with a single click"),
    _b("preview_open", True, "view", "Предпросмотр при запуске", "Preview on start", "Alt+P в окне.", "Alt+P in the window."),
    _e("date_format", "auto", "view", "Формат даты", "Date format",
       [("auto", "14.10.2026 09:05", "14/10/2026 09:05"), ("relative", "Сегодня, 09:05", "Today, 09:05"),
        ("long", "14 окт. 2026 г., 09:05", "14 Oct 2026, 09:05"), ("iso", "2026-10-14 09:05", "2026-10-14 09:05")]),
    _e("size_format", "auto", "view", "Формат размера", "Size format",
       [("auto", "Кратко (1,5 МБ)", "Short (1.5 MB)"), ("kb", "В КБ, как в Проводнике", "In KB, like Explorer"),
        ("decimal", "Десятичный (1 МБ = 1000 КБ)", "Decimal (1 MB = 1000 KB)")]),
    _b("shell_select_recent", True, "view", "Выделять только что скачанный файл", "Select the file you just downloaded",
       "Когда браузер или другая программа просит «Показать в папке», MemuFiles выделяет свежий файл в этой папке.",
       "When a browser or another app asks to “Show in folder”, MemuFiles selects the fresh file in that folder."),
    # ---------------------------------------------------------------- files
    _b("confirm_trash", False, "files", "Спрашивать перед удалением в корзину", "Ask before moving to the Recycle Bin",
       "Удаление всегда можно отменить Ctrl+Z.", "A delete can always be undone with Ctrl+Z."),
    _b("confirm_delete", True, "files", "Спрашивать перед удалением навсегда", "Ask before deleting permanently",
       "Shift+Delete — без корзины, отменить нельзя.", "Shift+Delete skips the Recycle Bin and cannot be undone."),
    _b("restore_session", True, "tabs", "Восстанавливать вкладки", "Restore tabs", "Открывать прошлые вкладки и папки при запуске.", "Reopen the last tabs and folders on start."),
    _e("start_page", "home", "tabs", "Новое окно открывает", "A new window opens",
       [("home", "Главную", "Home"), ("downloads", "Загрузки", "Downloads"), ("documents", "Документы", "Documents"),
        ("desktop", "Рабочий стол", "Desktop"), ("user", "Домашнюю папку", "Your user folder"),
        ("custom", "Свою папку", "A folder of your choice")]),
    {"key": "start_path", "type": "text", "default": "", "group": "tabs", "ru": "Своя папка", "en": "Your folder", "maxlen": 400,
     "placeholder": "D:\\Projects", "when": "start_page=custom",
     "desc_ru": "Полный путь к папке.", "desc_en": "The full path of the folder."},
    _e("new_tab_page", "home", "tabs", "Новая вкладка (Ctrl+T)", "New tab (Ctrl+T)",
       [("home", "Главная", "Home"), ("same", "Та же папка", "The same folder"), ("start", "Как новое окно", "Like a new window")]),
    _e("tab_after_close", "right", "tabs", "После закрытия вкладки", "After closing a tab",
       [("right", "Открыть вкладку справа", "Go to the tab on the right"), ("left", "Открыть вкладку слева", "Go to the tab on the left"),
        ("last", "Вернуться к прошлой", "Back to the previous one")]),
    _b("keep_running", False, "files", "Держать готовым в фоне", "Keep ready in the background",
       "После закрытия окна MemuFiles остаётся в памяти (~100 МБ) и следующее окно открывается мгновенно. "
       "Выключите, чтобы закрываться полностью.",
       "After you close the window MemuFiles stays in memory (~100 MB) so the next window opens instantly. "
       "Turn off to quit completely."),   # Round 39 (r39-files-perf)
    _b("search_content", False, "files", "Искать и по содержимому", "Search file contents too", "Медленнее, но находит текст внутри файлов.", "Slower, but finds text inside files."),
    _e("terminal", "auto", "files", "Терминал", "Terminal",
       [("auto", "Выбрать автоматически", "Pick automatically"), ("wt", "Windows Terminal", "Windows Terminal"),
        ("powershell", "PowerShell", "PowerShell"), ("cmd", "cmd", "cmd")], "Команда «Открыть в терминале».", "The “Open in terminal” command."),
    # ---------------------------------------------------------------- archives (r41-files)
    _b("open_archives", False, "archives", "Открывать архивы в MemuFiles", "Open archives in MemuFiles",
       "ZIP, 7z, RAR, TAR, GZ и другие открываются двойным щелчком в MemuFiles как папка — в Проводнике и других программах тоже. "
       "Выключите, чтобы вернуть прежнюю программу. Только для вашей учётной записи, без прав администратора.",
       "ZIP, 7z, RAR, TAR, GZ and more open in MemuFiles like a folder — from Explorer and other apps too. "
       "Turn off to bring back the previous app. Your account only, no administrator rights.", windows=True),
    _e("archive_click", "browse", "archives", "Двойной щелчок по архиву", "Double-clicking an archive",
       [("browse", "Открыть как папку", "Open like a folder"), ("extract", "Извлечь…", "Extract…"), ("system", "Открыть в программе Windows", "Open in the Windows app")]),
    _e("archive_format", "zip", "archives", "Формат нового архива", "New archive format",
       [("zip", "ZIP", "ZIP"), ("7z", "7z (нужен 7-Zip)", "7z (needs 7-Zip)"), ("tar.gz", "TAR.GZ", "TAR.GZ"), ("tar.xz", "TAR.XZ", "TAR.XZ"), ("tar", "TAR", "TAR")]),
    _e("archive_level", "normal", "archives", "Сжатие", "Compression",
       [("store", "Без сжатия", "Store"), ("fast", "Быстрое", "Fast"), ("normal", "Обычное", "Normal"), ("max", "Максимальное", "Maximum")]),
    _e("extract_to", "folder", "archives", "Извлекать по умолчанию", "Extract by default",
       [("folder", "В папку с именем архива", "To a folder named after the archive"), ("here", "Сюда", "Here")],
       "Если в архиве одна папка, она извлекается без лишней вложенности.", "When the archive holds a single folder it is extracted without an extra level."),
    _b("archive_delete_after", False, "archives", "Предлагать удалить архив после извлечения", "Offer to delete the archive after extracting"),
    # ---------------------------------------------------------------- AI
    _b("ai_enabled", True, "ai", "ИИ-ассистент", "AI assistant", "Вопросы о файлах и уборка по плану. Работает через MemuAI Proxy.",
       "Questions about files and planned tidy-ups. Works through MemuAI Proxy."),
    {"key": "ai_model", "type": "text", "default": "", "group": "ai", "ru": "Модель", "en": "Model", "maxlen": 120, "models": True,
     "desc_ru": "Пусто — модель по умолчанию в Proxy.", "desc_en": "Empty — the Proxy's default model.", "when": "ai_enabled"},
    {"key": "ai_fast_model", "type": "text", "default": "", "group": "ai", "ru": "Быстрая модель для документов",
     "en": "Fast model for documents", "maxlen": 120, "models": True, "when": "ai_enabled",
     "desc_ru": "Читает начало документов, когда ассистент раскладывает файлы по смыслу. Пусто — основная модель.",
     "desc_en": "Reads the start of documents when the assistant sorts files by meaning. Empty — the main model."},   # r40-files-max
    {"key": "proxy_url", "type": "url", "default": "", "group": "ai", "ru": "Адрес Proxy", "en": "Proxy address", "maxlen": 200,
     "desc_ru": "Только локальный адрес. Пусто — найти автоматически.", "desc_en": "Local address only. Empty — find it automatically.",
     "placeholder": "http://127.0.0.1:8317", "local_only": True},
    # ---------------------------------------------------------------- app state in the same file (not shown)
    {"key": "pinned", "type": "list", "default": [], "group": "advanced", "ru": "", "en": "", "hidden": True},
    {"key": "view_by_folder", "type": "map", "default": {}, "group": "advanced", "ru": "", "en": "", "hidden": True},
    # before round 41 «Папки сверху» was a switch; read as folders_order (False → «mixed»), never shown
    {"key": "folders_first", "type": "bool", "default": True, "group": "advanced", "ru": "", "en": "", "hidden": True},
    {"key": "views_rev", "type": "int", "default": 0, "group": "advanced", "ru": "", "en": "", "hidden": True, "min": 0, "max": 1000},
]

SHORTCUTS: List[Tuple[str, str, str]] = [
    ("Ctrl+T", "Новая вкладка", "New tab"), ("Ctrl+W", "Закрыть вкладку", "Close tab"), ("Ctrl+Shift+T", "Вернуть закрытую вкладку", "Reopen closed tab"),
    ("Ctrl+C / X / V", "Копировать / вырезать / вставить", "Copy / cut / paste"), ("F2", "Переименовать", "Rename"),
    ("Del / Shift+Del", "В корзину / удалить навсегда", "To Recycle Bin / delete permanently"), ("Ctrl+Z", "Отменить операцию", "Undo an operation"),
    ("Ctrl+Shift+N", "Новая папка", "New folder"), ("Ctrl+A", "Выделить всё", "Select all"), ("Ctrl+H", "Скрытые файлы", "Hidden files"),
    ("Ctrl+\\", "Две панели", "Two panes"), ("Alt+P", "Предпросмотр", "Preview"), ("Ctrl+Shift+A", "ИИ-ассистент", "AI assistant"),
    ("Ctrl+Shift+1…8", "Вид: значки, список, таблица, плитка, галерея", "View: icons, list, details, tiles, gallery"), ("Alt+← / Alt+→", "Назад / вперёд", "Back / forward"),
    ("Alt+↑", "Вверх на уровень", "Up one level"), ("F5", "Обновить", "Refresh"),
    ("Ctrl+N", "Новая вкладка с этой папкой", "New tab with this folder"), ("Ctrl+D", "В корзину", "To Recycle Bin"),
    ("Ctrl+L / Alt+D", "Адресная строка", "Address bar"), ("Backspace", "Назад", "Back"),
]

BY_KEY: Dict[str, Dict[str, Any]] = {item["key"]: item for item in SCHEMA}
DEFAULTS: Dict[str, Any] = {item["key"]: item["default"] for item in SCHEMA}
CHOICES: Dict[str, Tuple[Any, ...]] = {item["key"]: tuple(c["value"] for c in item["choices"]) for item in SCHEMA if item["type"] == "enum"}
RANGES: Dict[str, Tuple[int, int]] = {item["key"]: (item["min"], item["max"]) for item in SCHEMA if item["type"] == "int"}


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
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) != int(value):
            return False, None, "type"
        return (True, int(value), "") if item["min"] <= value <= item["max"] else (False, None, "range")
    if kind == "enum":
        return (True, value, "") if value in CHOICES[key] else (False, None, "choice")
    if not isinstance(value, str):
        return False, None, "type"
    value = value.strip()
    if len(value) > item.get("maxlen", 200):
        return False, None, "length"
    if kind == "url" and value and not _local_url(value):
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
