"""Catalog of Quick Ask assistant actions (Round 22, «команды ассистента»).

Pure data + validation, no OS calls: every action the assistant can perform is
described here once — its permission tier, its parameters (a small JSON-schema
subset that is also handed to models as a portable tool schema), its titles in
Russian and English and whether it can be undone. ``assistant_commands`` runs
them, ``assistant_intents`` maps phrases to them, the settings UI lists them.

Tiers
-----
``safe``       runs at once (volume, timers, opening an app…)
``confirm``    the user presses «Выполнить» on the card first (close an app, kill a
               process, empty the recycle bin, type text, Wi‑Fi off…)
``dangerous``  explicit confirmation AND a cancellable countdown (shutdown,
               restart, sleep, hibernate, sign out, the advanced shell)
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Tuple

TIERS = ("safe", "confirm", "dangerous")

CATEGORIES = {
    "apps": ("Приложения и файлы", "Apps and files"),
    "web": ("Интернет и сервисы", "Web and services"),
    "sound": ("Звук и медиа", "Sound and media"),
    "display": ("Экран", "Display"),
    "system": ("Система", "System"),
    "power": ("Питание", "Power"),
    "windows": ("Окна и рабочие столы", "Windows and desktops"),
    "time": ("Таймеры и напоминания", "Timers and reminders"),
    "memuai": ("MemuAI", "MemuAI"),
    "assistant": ("Ассистент", "Assistant"),
    "tools": ("Инструменты", "Tools"),
}

def _s(desc: str = "", **extra: Any) -> Dict[str, Any]:
    return {"type": "string", "description": desc, **extra}

def _n(desc: str = "", minimum: Optional[float] = None, maximum: Optional[float] = None,
       integer: bool = False) -> Dict[str, Any]:
    out: Dict[str, Any] = {"type": "integer" if integer else "number", "description": desc}
    if minimum is not None:
        out["minimum"] = minimum
    if maximum is not None:
        out["maximum"] = maximum
    return out

def _b(desc: str = "") -> Dict[str, Any]:
    return {"type": "boolean", "description": desc}

def _e(values: Tuple[str, ...], desc: str = "") -> Dict[str, Any]:
    return {"type": "string", "enum": list(values), "description": desc}

TARGET = _s("Window to act on: \"foreground\" (the window the user was working in, default) or an app name.")
SETTINGS_PAGES = {

    "": "ms-settings:",
    "display": "ms-settings:display", "sound": "ms-settings:sound", "bluetooth": "ms-settings:bluetooth",
    "wifi": "ms-settings:network-wifi", "network": "ms-settings:network-status",
    "update": "ms-settings:windowsupdate", "apps": "ms-settings:appsfeatures",
    "default_apps": "ms-settings:defaultapps", "startup": "ms-settings:startupapps",
    "battery": "ms-settings:batterysaver", "power": "ms-settings:powersleep",
    "mouse": "ms-settings:mousetouchpad", "keyboard": "ms-settings:keyboard",
    "personalization": "ms-settings:personalization", "background": "ms-settings:personalization-background",
    "colors": "ms-settings:colors", "themes": "ms-settings:themes", "lockscreen": "ms-settings:lockscreen",
    "taskbar": "ms-settings:taskbar", "notifications": "ms-settings:notifications",
    "focus": "ms-settings:quiethours", "privacy": "ms-settings:privacy", "camera": "ms-settings:privacy-webcam",
    "microphone": "ms-settings:privacy-microphone", "printers": "ms-settings:printers",
    "datetime": "ms-settings:dateandtime", "language": "ms-settings:regionlanguage",
    "storage": "ms-settings:storagesense", "about": "ms-settings:about", "nightlight": "ms-settings:nightlight",
    "vpn": "ms-settings:network-vpn", "airplane": "ms-settings:network-airplanemode",
    "accounts": "ms-settings:yourinfo", "multitasking": "ms-settings:multitasking",
    "gaming": "ms-settings:gaming-gamebar", "clipboard": "ms-settings:clipboard",
    "projection": "ms-settings:project", "accessibility": "ms-settings:easeofaccess",
    "recovery": "ms-settings:recovery", "troubleshoot": "ms-settings:troubleshoot",
}
FOLDERS = ("downloads", "documents", "desktop", "pictures", "music", "videos", "home", "appdata",
           "temp", "screenshots", "memuai")
MEMUAI_TARGETS = ("main", "new_chat", "settings", "quick_settings", "voice", "models", "appearance",
                  "panels", "modules", "integrations", "prompts", "remote", "work", "docs", "about", "apps")
SEARCH_ENGINES = ("google", "yandex", "duckduckgo", "bing")
MUSIC_SERVICES = ("youtube", "youtube_music", "yandex_music", "spotify", "vk", "soundcloud")
FOOD_SERVICES = ("yandex_eda", "delivery_club", "samokat", "kuper", "ubereats", "doordash", "deliveroo", "wolt")
SHOP_SERVICES = ("ozon", "wildberries", "yandex_market", "amazon", "aliexpress", "avito", "ebay")
TAXI_SERVICES = ("yandex_go", "uber", "bolt")
MAP_SERVICES = ("yandex_maps", "google_maps", "2gis")

def A(id: str, cat: str, tier: str, ru: str, en: str, desc: str, params: Optional[Dict[str, Any]] = None,
      required: Tuple[str, ...] = (), *, undo: bool = False, foreground: bool = False, windows: bool = True,
      advanced: bool = False, slow: bool = False) -> Dict[str, Any]:
    return {"id": id, "cat": cat, "tier": tier, "title": {"ru": ru, "en": en}, "desc": desc,
            "params": params or {}, "required": list(required), "undo": undo, "foreground": foreground,
            "windows": windows, "advanced": advanced, "slow": slow}

ACTIONS: List[Dict[str, Any]] = [

    A("app.open", "apps", "safe", "Открыть приложение", "Open an app",
      "Launch an installed program/app by its name (Start menu, App Paths, Store apps; ru/en names, fuzzy). "
      "Falls back to a known website when no app matches.",
      {"name": _s("App name as the user said it, e.g. \"телеграм\", \"Chrome\", \"калькулятор\"")}, ("name",)),
    A("app.close", "apps", "confirm", "Закрыть приложение", "Close an app",
      "Close every window of a running app politely (like clicking ×; unsaved work prompts stay).",
      {"name": _s("App name")}, ("name",)),
    A("app.switch", "apps", "safe", "Переключиться на приложение", "Switch to an app",
      "Bring a running app's window to the front.", {"name": _s("App name")}, ("name",)),
    A("app.list", "apps", "safe", "Открытые окна", "Open windows", "List the visible top-level windows."),
    A("open.url", "web", "safe", "Открыть ссылку", "Open a link",
      "Open an http(s) URL or a well-known site name in the default browser.",
      {"url": _s("URL or site name (\"youtube\", \"vk.com\")")}, ("url",), windows=False),
    A("open.path", "apps", "safe", "Открыть файл или папку", "Open a file or folder",
      "Open an existing file or folder with its default program. Executables need confirmation.",
      {"path": _s("Absolute path; %ENV% variables allowed")}, ("path",)),
    A("open.folder", "apps", "safe", "Открыть папку", "Open a folder",
      "Open a well-known folder in Explorer.", {"folder": _e(FOLDERS)}, ("folder",)),
    A("open.settings", "system", "safe", "Открыть параметры Windows", "Open Windows Settings",
      "Open a Windows Settings page.", {"page": _e(tuple(SETTINGS_PAGES), "Settings page; empty = home")}),
    A("web.search", "web", "safe", "Поиск в интернете", "Web search",
      "Open a web search in the browser (use the web_search tool instead when YOU need the facts).",
      {"query": _s("Search query"), "engine": _e(SEARCH_ENGINES)}, ("query",), windows=False),
    A("media.search", "web", "safe", "Найти музыку или видео", "Find music or video",
      "Open music/video search (YouTube, Yandex Music, Spotify, VK…) for a song, artist or video.",
      {"query": _s("What to find"), "service": _e(MUSIC_SERVICES)}, ("query",), windows=False),
    A("food.order", "web", "safe", "Заказать еду", "Order food",
      "Open a food delivery service with the search prefilled. MemuAI does NOT place orders or pay: the "
      "user finishes the order on the site.",
      {"query": _s("What to order, e.g. \"роллы\""), "service": _e(FOOD_SERVICES)}, ("query",), windows=False),
    A("shop.search", "web", "safe", "Найти товар", "Find a product",
      "Open a marketplace search (Ozon, Wildberries, Amazon…). Never buys anything.",
      {"query": _s("Product"), "service": _e(SHOP_SERVICES)}, ("query",), windows=False),
    A("taxi.order", "web", "safe", "Вызвать такси", "Call a taxi",
      "Open the taxi service (Yandex Go, Uber, Bolt) with the destination if given. Never books by itself.",
      {"to": _s("Destination"), "service": _e(TAXI_SERVICES)}, windows=False),
    A("maps.route", "web", "safe", "Маршрут на карте", "Route on a map",
      "Open a map with a place search or a route.",
      {"to": _s("Destination or place"), "from": _s("Start (optional)"), "service": _e(MAP_SERVICES)},
      ("to",), windows=False),
    A("weather.get", "web", "safe", "Погода", "Weather", "Current weather and forecast for a city.",
      {"city": _s("City; empty = the saved location / approximate"), "when": _e(("now", "today", "tomorrow", "week"))},
      windows=False),
    A("currency.convert", "web", "safe", "Курс валют", "Exchange rate", "Exchange rate / currency conversion.",
      {"amount": _n("Amount", 0), "from": _s("Currency code or name"), "to": _s("Currency code or name")},
      ("from",), windows=False),
    A("time.now", "assistant", "safe", "Текущее время", "Current time", "Local date and time.", windows=False),

    A("volume.get", "sound", "safe", "Громкость", "Volume", "Current master volume and mute state."),
    A("volume.set", "sound", "safe", "Изменить громкость", "Change volume",
      "Set the master volume to a level or change it by a delta (percent).",
      {"level": _n("0–100", 0, 100), "delta": _n("-100…100", -100, 100)}, undo=True),
    A("volume.mute", "sound", "safe", "Выключить звук", "Mute",
      "Mute/unmute the master volume.", {"mute": _b("true = mute, false = unmute; omit = toggle")}, undo=True),
    A("media.key", "sound", "safe", "Управление воспроизведением", "Media control",
      "Press a media key for the current player.", {"key": _e(("play_pause", "next", "prev", "stop"))}, ("key",)),

    A("brightness.get", "display", "safe", "Яркость", "Brightness", "Current screen brightness."),
    A("brightness.set", "display", "safe", "Изменить яркость", "Change brightness",
      "Set screen brightness (laptop panel via WMI, external monitors via DDC/CI).",
      {"level": _n("0–100", 0, 100), "delta": _n("-100…100", -100, 100)}, undo=True),
    A("theme.set", "display", "safe", "Тёмная/светлая тема Windows", "Windows dark/light mode",
      "Switch the Windows app/system color mode.", {"mode": _e(("dark", "light", "toggle"))}, ("mode",), undo=True),
    A("nightlight.open", "display", "safe", "Ночной свет", "Night light",
      "Windows has no public API for Night light: opens its settings page."),

    A("wifi.set", "system", "confirm", "Wi‑Fi вкл/выкл", "Wi‑Fi on/off", "Turn the Wi‑Fi radio on or off.",
      {"on": _b("true = on, false = off")}, ("on",), undo=True),
    A("bluetooth.set", "system", "safe", "Bluetooth вкл/выкл", "Bluetooth on/off",
      "Turn the Bluetooth radio on or off.", {"on": _b("true = on, false = off")}, ("on",), undo=True),
    A("screenshot.take", "system", "safe", "Скриншот", "Screenshot",
      "Save a screenshot (without MemuAI windows) to Pictures\\Screenshots, or start the region snipping tool.",
      {"mode": _e(("screen", "region", "window")), "copy": _b("Also copy to the clipboard")}),
    A("clipboard.get", "system", "safe", "Буфер обмена", "Clipboard", "Read the text in the clipboard."),
    A("clipboard.set", "system", "safe", "Скопировать в буфер", "Copy to clipboard",
      "Put text into the clipboard (clear=true empties it).", {"text": _s("Text"), "clear": _b("Empty the clipboard")},
      (), undo=True),
    A("type.text", "system", "confirm", "Напечатать текст", "Type text",
      "Type text into the window the user was working in.", {"text": _s("Text to type")}, ("text",), foreground=True),
    A("keys.press", "system", "confirm", "Нажать сочетание клавиш", "Press a key combination",
      "Press a key combination in the foreground window, e.g. \"Ctrl+Shift+T\".",
      {"keys": _s("Combination")}, ("keys",), foreground=True),
    A("recycle.empty", "system", "confirm", "Очистить корзину", "Empty the Recycle Bin",
      "Permanently delete everything in the Recycle Bin."),
    A("sysinfo.get", "system", "safe", "Сведения о системе", "System info",
      "CPU, memory, battery, disks, network, uptime.",
      {"what": _e(("all", "cpu", "ram", "battery", "disk", "network", "uptime"))}),
    A("process.list", "system", "safe", "Процессы", "Processes",
      "List running processes (top by memory, or filtered).", {"query": _s("Filter by name")}),
    A("process.kill", "system", "confirm", "Завершить процесс", "End a process",
      "Terminate processes by name or PID (system processes are refused).",
      {"name": _s("Process/app name"), "pid": _n("PID", 1, integer=True)}),
    A("lock.screen", "power", "safe", "Заблокировать компьютер", "Lock the computer", "Lock the workstation."),
    A("power.sleep", "power", "dangerous", "Спящий режим", "Sleep", "Put the computer to sleep."),
    A("power.hibernate", "power", "dangerous", "Гибернация", "Hibernate", "Hibernate the computer."),
    A("power.restart", "power", "dangerous", "Перезагрузка", "Restart", "Restart the computer (optionally later).",
      {"delay_minutes": _n("Minutes from now (omit = in 5 s)", 0, 10080, integer=True)}),
    A("power.shutdown", "power", "dangerous", "Выключение", "Shut down", "Shut the computer down (optionally later).",
      {"delay_minutes": _n("Minutes from now (omit = in 5 s)", 0, 10080, integer=True)}),
    A("power.signout", "power", "dangerous", "Выход из системы", "Sign out", "Sign the user out of Windows."),
    A("power.cancel", "power", "safe", "Отменить выключение", "Cancel shutdown",
      "Abort a pending shutdown/restart."),
    A("shell.run", "system", "dangerous", "Команда PowerShell", "PowerShell command",
      "Run a PowerShell command (only when the user enabled the advanced mode). Output is returned.",
      {"command": _s("PowerShell command")}, ("command",), advanced=True, slow=True),

    A("window.action", "windows", "safe", "Действие с окном", "Window action",
      "Maximize, minimize, restore, snap or center a window.",
      {"action": _e(("maximize", "minimize", "restore", "snap_left", "snap_right", "snap_top", "snap_bottom",
                     "center", "close", "topmost", "untopmost")), "target": TARGET}, ("action",), foreground=True),
    A("window.move", "windows", "safe", "Перенести окно на монитор", "Move a window to a monitor",
      "Move a window to another monitor (keeps its relative size).",
      {"monitor": _s("\"next\", \"prev\" or a number from 1"), "target": TARGET}, foreground=True),
    A("window.resize", "windows", "safe", "Размер окна", "Window size",
      "Resize/place a window: width/height in percent of the monitor work area.",
      {"width": _n("% of work area", 10, 100), "height": _n("% of work area", 10, 100), "target": TARGET},
      foreground=True),
    A("window.arrange", "windows", "safe", "Расставить окна", "Arrange windows",
      "Tile the visible windows of the current monitor.",
      {"layout": _e(("side_by_side", "grid", "stack", "cascade"))}),
    A("window.minimize_all", "windows", "safe", "Свернуть все окна", "Minimize all windows",
      "Show the desktop (minimize all).", undo=True),
    A("desktop.virtual", "windows", "safe", "Виртуальные рабочие столы", "Virtual desktops",
      "Create, switch or close a Windows virtual desktop.",
      {"action": _e(("new", "next", "prev", "close", "view"))}, ("action",)),
    A("layout.capture", "windows", "safe", "Запомнить раскладку окон", "Save the window layout",
      "Capture the arrangement of visible windows (app, monitor, position, state) under a name.",
      {"name": _s("Layout name")}),
    A("layout.restore", "windows", "safe", "Восстановить раскладку окон", "Restore a window layout",
      "Restore a saved layout: launch missing apps, wait for windows and place them.",
      {"name": _s("Layout name")}, ("name",), slow=True),

    A("timer.start", "time", "safe", "Таймер", "Timer", "Start a named countdown timer.",
      {"seconds": _n("Duration in seconds", 1, 86400 * 7, integer=True), "label": _s("Name, e.g. \"чай\"")},
      ("seconds",), windows=False, undo=True),
    A("timer.cancel", "time", "safe", "Отменить таймер", "Cancel a timer",
      "Cancel timers by label, id, or all.", {"label": _s("Label or id"), "all": _b()}, windows=False),
    A("timer.pause", "time", "safe", "Пауза таймера", "Pause a timer",
      "Pause or resume a timer.", {"label": _s("Label or id"), "resume": _b()}, windows=False),
    A("timer.list", "time", "safe", "Таймеры", "Timers", "List timers, alarms, stopwatch and Pomodoro.", windows=False),
    A("stopwatch.control", "time", "safe", "Секундомер", "Stopwatch", "Control the stopwatch.",
      {"action": _e(("start", "stop", "reset", "lap"))}, ("action",), windows=False),
    A("alarm.set", "time", "safe", "Будильник", "Alarm", "Ring at a clock time (today or the next day).",
      {"time": _s("HH:MM, 24h"), "date": _s("YYYY-MM-DD, optional"), "label": _s("Label")}, ("time",),
      windows=False, undo=True),
    A("pomodoro.control", "time", "safe", "Pomodoro", "Pomodoro",
      "Start/stop/pause/skip a Pomodoro cycle or show its stats.",
      {"action": _e(("start", "stop", "pause", "resume", "skip", "stats")),
       "work": _n("Work minutes", 1, 180, integer=True), "short_break": _n("Short break minutes", 1, 60, integer=True),
       "long_break": _n("Long break minutes", 1, 120, integer=True), "cycles": _n("Cycles", 1, 12, integer=True)},
      ("action",), windows=False),
    A("reminder.create", "time", "safe", "Напоминание", "Reminder",
      "Remind the user at a time: on this computer (notification + sound) and/or in Telegram (the paired "
      "MemuAI bot, sent by the scheduler of MemuAI Proxy).",
      {"text": _s("What to remind about"), "at": _s("Local date-time ISO 'YYYY-MM-DDTHH:MM' or 'HH:MM'"),
       "in_minutes": _n("Or: minutes from now", 0, 525600), "channel": _e(("local", "telegram", "both"))},
      ("text",), windows=False, undo=True),
    A("reminder.list", "time", "safe", "Напоминания", "Reminders", "List pending reminders.", windows=False),
    A("reminder.cancel", "time", "safe", "Отменить напоминание", "Cancel a reminder",
      "Cancel a reminder by id or text.", {"id": _s("Reminder id"), "text": _s("Text to match")}, windows=False),
    A("schedule.plan", "time", "safe", "Задача по расписанию", "Scheduled task",
      "Plan a repeating/conditional task from the user's words. Shows a preview card the user confirms.",
      {"text": _s("The phrase")}, ("text",), windows=False, slow=True),

    A("image.generate", "memuai", "safe", "Сгенерировать изображение", "Generate an image",
      "Generate an image with the image model chosen for MemuAI (same pipeline as chat/Studio).",
      {"prompt": _s("Image description"), "model": _s("Image model id (optional)"),
       "aspect": _e(("auto", "1:1", "16:9", "9:16", "4:3", "3:4"))}, ("prompt",), windows=False, slow=True),
    A("note.add", "memuai", "safe", "Заметка", "Note",
      "Save a quick note into the MemuAI chat «Заметки».", {"text": _s("Note text")}, ("text",),
      windows=False, undo=True),
    A("memuai.chat", "memuai", "safe", "Отправить в чат MemuAI", "Send to MemuAI chat",
      "Open a new MemuAI chat with this text in the composer.", {"text": _s("Text")}, ("text",), windows=False),
    A("memuai.open", "memuai", "safe", "Открыть раздел MemuAI", "Open a MemuAI section",
      "Open the main MemuAI window at a section/settings tab.", {"target": _e(MEMUAI_TARGETS)}, windows=False),

    A("assistant.undo", "assistant", "safe", "Отменить последнее действие", "Undo the last action",
      "Undo the most recent undoable action.", windows=False),
    A("assistant.help", "assistant", "safe", "Что я умею", "What I can do", "Show the command list.", windows=False),
    A("command.run", "assistant", "safe", "Запустить свою команду", "Run a custom command",
      "Run one of the user's saved custom commands (routines) by name.", {"name": _s("Command name")}, ("name",),
      windows=False, slow=True),
]

BY_ID: Dict[str, Dict[str, Any]] = {row["id"]: row for row in ACTIONS}

def get(action_id: str) -> Optional[Dict[str, Any]]:
    return BY_ID.get(str(action_id or ""))

def title(action_id: str, lang: str = "ru") -> str:
    row = get(action_id)
    if not row:
        return str(action_id)
    return row["title"]["en" if lang == "en" else "ru"]

class ParamError(ValueError):
    pass

def validate_params(action_id: str, params: Any) -> Dict[str, Any]:
    """Clean ``params`` against the action schema; unknown keys are dropped. Raises ParamError."""
    row = get(action_id)
    if not row:
        raise ParamError(f"unknown action {action_id!r}")
    raw = params if isinstance(params, dict) else {}
    out: Dict[str, Any] = {}
    for key, spec in row["params"].items():
        if key not in raw or raw[key] is None or raw[key] == "":
            continue
        value = raw[key]
        kind = spec.get("type")
        if kind == "string":
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                value = str(value)
            if not isinstance(value, str):
                raise ParamError(f"{key}: expected text")
            value = value.strip()
            limit = 20000 if key in ("text", "command", "prompt") else 2000
            value = value[:limit]
            if "enum" in spec:
                low = value.lower()
                if low not in spec["enum"]:
                    raise ParamError(f"{key}: one of {', '.join(spec['enum'][:12])}")
                value = low
            if not value:
                continue
        elif kind in ("number", "integer"):
            if isinstance(value, bool):
                raise ParamError(f"{key}: expected a number")
            try:
                number = float(str(value).replace(",", ".").rstrip("%").strip()) if isinstance(value, str) else float(value)
            except (TypeError, ValueError):
                raise ParamError(f"{key}: expected a number") from None
            if not math.isfinite(number):
                raise ParamError(f"{key}: expected a finite number")
            if "minimum" in spec:
                number = max(float(spec["minimum"]), number)
            if "maximum" in spec:
                number = min(float(spec["maximum"]), number)
            value = int(round(number)) if kind == "integer" else (int(number) if number.is_integer() else number)
        elif kind == "boolean":
            if isinstance(value, str):
                low = value.strip().lower()
                if low in ("1", "true", "yes", "on", "да", "вкл"):
                    value = True
                elif low in ("0", "false", "no", "off", "нет", "выкл"):
                    value = False
                else:
                    raise ParamError(f"{key}: expected true/false")
            else:
                value = bool(value)
        out[key] = value
    missing = [key for key in row["required"] if key not in out]
    if missing:
        raise ParamError("missing: " + ", ".join(missing))
    return out

def openai_tools(actions: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Portable per-action function schemas (OpenAI ``tools`` format)."""
    rows = [BY_ID[a] for a in actions if a in BY_ID] if actions else ACTIONS
    out = []
    for row in rows:
        props = {}
        for key, spec in row["params"].items():
            props[key] = {k: v for k, v in spec.items() if v not in ("", None)}
        out.append({"type": "function", "function": {
            "name": row["id"].replace(".", "_"),
            "description": f"[{row['tier']}] {row['desc']}",
            "parameters": {"type": "object", "properties": props, "required": list(row["required"]),
                           "additionalProperties": False}}})
    return out

def tool_name_to_action(name: str) -> str:
    """``volume_set`` → ``volume.set`` (inverse of :func:`openai_tools` naming)."""
    text = str(name or "")
    if text in BY_ID:
        return text
    for action_id in BY_ID:
        if action_id.replace(".", "_") == text:
            return action_id
    return ""

def compact_catalog(enabled: Optional[List[str]] = None) -> str:
    """One line per action for the Quick Ask system prompt: ``id(param, param?) [tier] — description``."""
    lines = []
    for row in ACTIONS:
        if enabled is not None and row["id"] not in enabled:
            continue
        parts = []
        for key, spec in row["params"].items():
            label = key + ("" if key in row["required"] else "?")
            if spec.get("enum"):
                values = [v for v in spec["enum"] if v]
                label += "=" + "|".join(values[:10]) + ("|…" if len(values) > 10 else "")
            parts.append(label)
        tier = "" if row["tier"] == "safe" else f" [{row['tier']}]"
        desc = row["desc"].split(". ")[0].rstrip(".")
        lines.append(f"  {row['id']}({', '.join(parts)}){tier} — {desc}")
    return "\n".join(lines)

def public(lang: str = "ru") -> List[Dict[str, Any]]:
    """The catalog for the settings UI."""
    key = "en" if lang == "en" else "ru"
    return [{"id": row["id"], "cat": row["cat"], "tier": row["tier"], "title": row["title"][key],
             "desc": row["desc"], "params": row["params"], "required": row["required"],
             "undo": row["undo"], "windows": row["windows"], "advanced": row["advanced"]} for row in ACTIONS]

def categories(lang: str = "ru") -> Dict[str, str]:
    return {key: value[1 if lang == "en" else 0] for key, value in CATEGORIES.items()}

_SAFE_ID = re.compile(r"^[a-z_]+\.[a-z_]+$")
assert all(_SAFE_ID.match(row["id"]) for row in ACTIONS)
assert all(row["tier"] in TIERS for row in ACTIONS)
assert all(row["cat"] in CATEGORIES for row in ACTIONS)
