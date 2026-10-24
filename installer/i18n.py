"""Native (non-HTML) strings of the installer: shortcut names, message boxes, CLI errors.

The HTML interface has its own dictionary in ``ui/i18n.js``; both are kept to
the same two languages, and ``tests/installer_engine_test.py`` checks that
every key exists in both.
"""
from __future__ import annotations

from typing import Dict

STRINGS: Dict[str, Dict[str, str]] = {
    "uninstall_shortcut": {"en": "Uninstall MemuAI", "ru": "Удалить MemuAI"},
    "uninstall_shortcut_proxy": {"en": "Uninstall MemuAI Proxy", "ru": "Удалить MemuAI Proxy"},
    "safe_mode_shortcut": {"en": "MemuAI (safe mode)", "ru": "MemuAI (безопасный режим)"},
    "setup_title": {"en": "{name} Setup", "ru": "Установка {name}"},
    "uninstall_title": {"en": "Uninstall {name}", "ru": "Удаление {name}"},
    "webview_missing": {
        "en": ("The Setup interface and {name} itself need the Microsoft Edge WebView2 Runtime, "
               "which is not installed on this PC.\n\n"
               "Yes — download and install WebView2 from Microsoft (about 2 MB, then the runtime), then continue.\n"
               "No — install {name} with the default settings without the visual interface.\n"
               "Cancel — exit Setup."),
        "ru": ("Интерфейсу установщика и самому {name} нужен Microsoft Edge WebView2 Runtime, "
               "но он не установлен на этом компьютере.\n\n"
               "Да — скачать и установить WebView2 с сайта Microsoft (около 2 МБ, затем сама среда) и продолжить.\n"
               "Нет — установить {name} с настройками по умолчанию без визуального интерфейса.\n"
               "Отмена — выйти из установщика."),
    },
    "webview_failed": {
        "en": "WebView2 could not be installed ({error}).\n\nInstall {name} with the default settings without the visual interface?",
        "ru": "Не удалось установить WebView2 ({error}).\n\nУстановить {name} с настройками по умолчанию без визуального интерфейса?",
    },
    "ui_failed": {
        "en": "The Setup window could not be opened ({error}).\n\nInstall {name} with the default settings without the visual interface?",
        "ru": "Не удалось открыть окно установщика ({error}).\n\nУстановить {name} с настройками по умолчанию без визуального интерфейса?",
    },
    "uninstall_ui_failed": {
        "en": "The uninstaller window could not be opened ({error}).\n\nRemove {name} now? Your personal data will be kept.",
        "ru": "Не удалось открыть окно удаления ({error}).\n\nУдалить {name} сейчас? Личные данные будут сохранены.",
    },
    "basic_done": {"en": "{name} {version} is installed.", "ru": "{name} {version} установлен."},
    "basic_uninstalled": {"en": "{name} has been removed. Your personal data was kept.",
                          "ru": "{name} удалён. Личные данные сохранены."},
    "basic_failed": {"en": "Setup failed: {error}\n\nLog: {log}", "ru": "Ошибка установки: {error}\n\nЖурнал: {log}"},
    "no_payload": {
        "en": "This Setup file is incomplete or damaged ({error}). Download MemuAI Setup again.",
        "ru": "Файл установщика неполный или повреждён ({error}). Скачайте установщик MemuAI заново.",
    },
    "not_installed": {"en": "{name} is not installed in {path}.", "ru": "{name} не установлен в {path}."},
    "already_running": {"en": "MemuAI Setup is already running.", "ru": "Установщик MemuAI уже запущен."},
    "apps_running": {
        "en": "Close {apps} and try again.",
        "ru": "Закройте {apps} и повторите попытку.",
    },
}


def tr(key: str, lang: str = "en", **values: object) -> str:
    entry = STRINGS.get(key) or {}
    text = entry.get(lang) or entry.get("en") or key
    try:
        return text.format(**values)
    except (KeyError, IndexError):
        return text


def pick_language(code: str) -> str:
    """Map a locale/LCID-derived code to the interface language (ru or en)."""
    code = (code or "").lower().replace("_", "-")
    return "ru" if code.split("-", 1)[0] in {"ru", "be", "kk"} else "en"
