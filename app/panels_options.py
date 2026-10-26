# -*- coding: utf-8 -*-
"""Настройка содержимого панелей (Раунд 23): что показывать, как и в каких единицах.

Одна схема на вид панели — источник правды и для бэкенда (``clean_options`` чистит то, что
пришло из окна или от модели), и для окна (схема уходит в ``GET /api/pg/panels`` как
``option_schema``, редактор панели рисует по ней элементы управления). Поэтому новая опция —
это одна строка здесь, а не правка в двух местах.

Опция: ``{key, type, default, label:[ru, en], hint?:[ru, en], group: main|more, choices?,
min?, max?, step?, unit?, when?: {key: [значения]}, refetch?: bool}``.

* ``type``: ``bool`` · ``choice`` (одно из ``choices``; ``style: seg`` — сегменты) · ``multi``
  (упорядоченный набор из ``choices``) · ``int`` · ``text`` · ``list`` (строки, ``item_re``)
  · ``map`` (метрика → вид, для «Производительности ПК»).
* ``group: more`` — прячется под «Дополнительно» (прогрессивное раскрытие).
* ``refetch`` — после изменения нужен новый запрос к источнику (иначе меняется только вид).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

C = lambda value, ru, en: [value, ru, en]  # noqa: E731

SYSTEM_METRICS = [
    C("cpu", "Процессор — общая загрузка", "CPU — total load"),
    C("cores", "Ядра по отдельности", "Per-core load"),
    C("ram", "Оперативная память", "Memory"),
    C("swap", "Файл подкачки", "Swap / page file"),
    C("gpu", "Видеокарта — загрузка", "GPU — load"),
    C("vram", "Видеопамять", "GPU memory"),
    C("gpu_temp", "Температура видеокарты", "GPU temperature"),
    C("disks", "Диски — занято", "Disks — used space"),
    C("disk_io", "Диски — скорость чтения/записи", "Disks — read/write speed"),
    C("net", "Сеть — приём и отдача", "Network — down and up"),
    C("temps", "Температуры датчиков", "Sensor temperatures"),
    C("fans", "Вентиляторы", "Fans"),
    C("battery", "Батарея", "Battery"),
    C("uptime", "Время работы", "Uptime"),
    C("procs", "Самые активные процессы", "Top processes"),
]
SYSTEM_DEFAULT = ["cpu", "ram", "gpu", "disks", "net", "disk_io", "temps", "battery"]

SYSTEM_VIEWS = {
    "cpu": ["ring", "bar", "number", "chart"], "ram": ["ring", "bar", "number", "chart"],
    "swap": ["ring", "bar", "number"], "gpu": ["ring", "bar", "number", "chart"],
    "vram": ["ring", "bar", "number"], "gpu_temp": ["number", "bar", "chart"],
    "disks": ["ring", "bar", "number"], "disk_io": ["number", "chart"], "net": ["number", "chart"],
    "temps": ["number", "bar"], "fans": ["number"], "battery": ["ring", "bar", "number"],
    "uptime": ["number"], "procs": ["list"], "cores": ["bars"],
}
VIEW_NAMES = {"ring": ("Кольцо", "Ring"), "bar": ("Полоса", "Bar"), "number": ("Число", "Number"),
              "chart": ("График", "Chart"), "bars": ("Столбики", "Bars"), "list": ("Список", "List"),
              "auto": ("Авто", "Auto")}

WEATHER_FIELDS = [
    C("feels", "Ощущается как", "Feels like"), C("hilo", "Макс / мин за день", "Day high / low"),
    C("wind", "Ветер", "Wind"), C("humidity", "Влажность", "Humidity"),
    C("precip", "Вероятность осадков", "Chance of rain"), C("pressure", "Давление", "Pressure"),
    C("uv", "УФ-индекс", "UV index"), C("sun", "Восход и закат", "Sunrise and sunset"),
    C("visibility", "Видимость", "Visibility"),
]

USAGE_WINDOWS = [C("five_hour", "Окно 5 часов", "5-hour window"), C("weekly", "Неделя", "Week"),
                 C("other", "Остальные (модели, Opus…)", "Others (models, Opus…)")]

PROXY_FIELDS = [C("requests", "Запросы", "Requests"), C("success", "Успешность", "Success rate"),
                C("tokens", "Токены", "Tokens"), C("latency", "Задержка (p50 / p95)", "Latency (p50 / p95)"),
                C("cost", "Стоимость", "Cost"), C("chart", "График запросов", "Requests chart"),
                C("models", "Топ моделей", "Top models")]

TIMER_PARTS = [C("pomodoro", "Помодоро", "Pomodoro"), C("timers", "Таймеры и напоминания", "Timers and reminders"),
               C("stopwatch", "Секундомер", "Stopwatch")]

LIVE = {"key": "live", "type": "choice", "default": "2", "style": "seg", "group": "main",
        "label": ["Живой режим", "Live mode"],
        "hint": ["Пока карточка видна, значения обновляются прямо в окне. Свёрнутое окно ничего не опрашивает.",
                 "While the card is visible values update in place. A hidden window polls nothing."],
        "choices": [C("off", "Выкл.", "Off"), C("1", "1 с", "1 s"), C("2", "2 с", "2 s"), C("5", "5 с", "5 s")]}

SCHEMA: Dict[str, List[Dict[str, Any]]] = {
    "system": [
        {"key": "metrics", "type": "multi", "default": SYSTEM_DEFAULT, "group": "main", "choices": SYSTEM_METRICS,
         "label": ["Что показывать", "What to show"],
         "hint": ["Порядок — как в списке; перетащите строки, чтобы поменять.", "Shown in this order; drag rows to reorder."]},
        {"key": "style", "type": "choice", "default": "rings", "style": "seg", "group": "main",
         "label": ["Вид", "Look"], "choices": [C("rings", "Кольца", "Rings"), C("bars", "Полосы", "Bars"),
                                               C("numbers", "Числа", "Numbers"), C("charts", "Графики", "Charts")]},
        LIVE,
        {"key": "history", "type": "choice", "default": "60", "group": "more", "label": ["Длина графиков", "Chart length"],
         "hint": ["Сколько последних секунд живого режима видно на графике.", "How many recent seconds of live mode a chart shows."],
         "choices": [C("30", "30 с", "30 s"), C("60", "1 мин", "1 min"), C("120", "2 мин", "2 min"),
                     C("300", "5 мин", "5 min"), C("900", "15 мин", "15 min")]},
        {"key": "views", "type": "map", "default": {}, "group": "more", "label": ["Вид каждой метрики", "Look per metric"],
         "hint": ["«Авто» — как выбрано в «Вид».", "“Auto” follows the Look above."], "views": SYSTEM_VIEWS},
        {"key": "disk_mode", "type": "choice", "default": "main", "group": "more", "label": ["Диски", "Disks"],
         "choices": [C("main", "Системный", "System disk"), C("all", "Каждый отдельно", "Each disk")],
         "when": {"metrics": ["disks"]}},
        {"key": "disk_filter", "type": "text", "default": "", "group": "more", "max": 80,
         "label": ["Только эти диски", "Only these disks"], "placeholder": ["C:, D:", "C:, D:"],
         "when": {"disk_mode": ["all"]}},
        {"key": "net_mode", "type": "choice", "default": "total", "group": "more", "label": ["Сеть", "Network"],
         "choices": [C("total", "Всего", "Total"), C("each", "Каждый адаптер", "Each adapter")], "when": {"metrics": ["net"]}},
        {"key": "net_filter", "type": "text", "default": "", "group": "more", "max": 80,
         "label": ["Только адаптеры", "Only adapters"], "placeholder": ["Ethernet, Wi-Fi", "Ethernet, Wi-Fi"],
         "when": {"net_mode": ["each"]}},
        {"key": "net_units", "type": "choice", "default": "bytes", "style": "seg", "group": "more",
         "label": ["Скорость сети", "Network speed"], "choices": [C("bytes", "МБ/с", "MB/s"), C("bits", "Мбит/с", "Mbit/s")],
         "when": {"metrics": ["net"]}},
        {"key": "temp_unit", "type": "choice", "default": "auto", "style": "seg", "group": "more",
         "label": ["Температура", "Temperature"], "choices": [C("auto", "Как в системе", "System"), C("c", "°C", "°C"), C("f", "°F", "°F")]},
        {"key": "procs_count", "type": "int", "default": 5, "min": 3, "max": 10, "group": "more",
         "label": ["Сколько процессов", "How many processes"], "when": {"metrics": ["procs"]}},
        {"key": "procs_sort", "type": "choice", "default": "cpu", "style": "seg", "group": "more",
         "label": ["Сортировать процессы", "Sort processes by"], "choices": [C("cpu", "по CPU", "CPU"), C("mem", "по памяти", "memory")],
         "when": {"metrics": ["procs"]}},
    ],
    "weather": [
        {"key": "fields", "type": "multi", "default": ["feels", "hilo", "wind", "humidity"], "group": "main",
         "choices": WEATHER_FIELDS, "label": ["Подробности", "Details"], "refetch": True},
        {"key": "forecast", "type": "choice", "default": "auto", "group": "main", "label": ["Прогноз", "Forecast"],
         "choices": [C("auto", "По дням на большой карточке", "Daily on a large card"), C("none", "Без прогноза", "None"),
                     C("hourly", "По часам", "Hourly"), C("daily", "По дням", "Daily"), C("both", "По часам и по дням", "Hourly and daily")],
         "refetch": True},
        {"key": "days", "type": "int", "default": 5, "min": 3, "max": 7, "group": "more", "label": ["Дней в прогнозе", "Forecast days"],
         "refetch": True},
        {"key": "hours", "type": "int", "default": 12, "min": 6, "max": 24, "step": 3, "group": "more",
         "label": ["Часов в прогнозе", "Forecast hours"], "refetch": True, "when": {"forecast": ["hourly", "both"]}},
        {"key": "units", "type": "choice", "default": "auto", "style": "seg", "group": "more", "label": ["Температура", "Temperature"],
         "choices": [C("auto", "Как в настройках", "As in settings"), C("metric", "°C", "°C"), C("imperial", "°F", "°F")], "refetch": True},
        {"key": "wind_unit", "type": "choice", "default": "auto", "group": "more", "label": ["Скорость ветра", "Wind speed"],
         "choices": [C("auto", "Авто", "Auto"), C("ms", "м/с", "m/s"), C("kmh", "км/ч", "km/h"), C("mph", "mph", "mph"),
                     C("kn", "узлы", "knots")], "refetch": True},
        {"key": "pressure_unit", "type": "choice", "default": "mmhg", "style": "seg", "group": "more", "label": ["Давление", "Pressure"],
         "choices": [C("mmhg", "мм рт. ст.", "mmHg"), C("hpa", "гПа", "hPa"), C("inhg", "inHg", "inHg")], "refetch": True,
         "when": {"fields": ["pressure"]}},
    ],
    "currency": [
        {"key": "extra", "type": "list", "default": [], "group": "main", "max_items": 6, "item_re": r"^[A-Za-z]{3}$",
         "label": ["Ещё валюты", "More currencies"], "placeholder": ["EUR, CNY, KZT", "EUR, CNY, KZT"],
         "hint": ["Курсы этих валют к той же валюте «В» — строками под основным.", "Rates of these to the same target, as rows below."],
         "refetch": True},
        {"key": "period", "type": "choice", "default": "30", "style": "seg", "group": "main", "label": ["График", "Chart"],
         "choices": [C("off", "Нет", "Off"), C("7", "Неделя", "Week"), C("30", "Месяц", "Month"), C("90", "3 мес.", "3 mo"),
                     C("365", "Год", "Year")], "refetch": True},
        {"key": "digits", "type": "choice", "default": "auto", "style": "seg", "group": "more", "label": ["Знаков после запятой", "Decimals"],
         "choices": [C("auto", "Авто", "Auto"), C("2", "2", "2"), C("4", "4", "4")], "refetch": True},
        {"key": "show_delta", "type": "bool", "default": True, "group": "more", "label": ["Изменение за день", "Daily change"]},
    ],
    "crypto": [
        {"key": "extra", "type": "list", "default": [], "group": "main", "max_items": 6, "item_re": r"^[a-z0-9-]{2,60}$",
         "label": ["Ещё монеты", "More coins"], "placeholder": ["ethereum, solana", "ethereum, solana"],
         "hint": ["Идентификаторы CoinGecko через запятую.", "CoinGecko ids, comma-separated."], "refetch": True},
        {"key": "period", "type": "choice", "default": "1", "style": "seg", "group": "main", "label": ["График", "Chart"],
         "choices": [C("off", "Нет", "Off"), C("1", "Сутки", "Day"), C("7", "Неделя", "Week"), C("30", "Месяц", "Month"),
                     C("365", "Год", "Year")], "refetch": True},
        {"key": "fields", "type": "multi", "default": ["change"], "group": "more", "label": ["Подробности", "Details"],
         "choices": [C("change", "Изменение за 24 ч", "24h change"), C("mcap", "Капитализация", "Market cap"),
                     C("volume", "Объём за 24 ч", "24h volume")], "refetch": True},
    ],
    "stock": [
        {"key": "extra", "type": "list", "default": [], "group": "main", "max_items": 5, "item_re": r"^[A-Za-z0-9.\-^=]{1,20}$",
         "label": ["Ещё тикеры", "More tickers"], "placeholder": ["GAZP, LKOH", "MSFT, NVDA"],
         "hint": ["С той же биржи, строками под основным.", "Same exchange, as rows below."], "refetch": True},
        {"key": "period", "type": "choice", "default": "1d", "style": "seg", "group": "main", "label": ["График", "Chart"],
         "choices": [C("off", "Нет", "Off"), C("1d", "День", "Day"), C("5d", "5 дней", "5 days"), C("1mo", "Месяц", "Month"),
                     C("6mo", "Полгода", "6 mo"), C("1y", "Год", "Year")], "refetch": True},
    ],
    "rss": [
        {"key": "count", "type": "int", "default": 6, "min": 3, "max": 20, "group": "main", "label": ["Сколько заголовков", "How many headlines"]},
        {"key": "extra", "type": "list", "default": [], "group": "main", "max_items": 5, "item_re": r"^https?://\S+$",
         "label": ["Ещё ленты", "More feeds"], "placeholder": ["https://…/rss", "https://…/rss"],
         "hint": ["Записи всех лент — вместе, по времени.", "Entries of all feeds merged by time."], "refetch": True},
        {"key": "show_time", "type": "bool", "default": True, "group": "more", "label": ["Время публикации", "Publication time"]},
        {"key": "show_desc", "type": "bool", "default": False, "group": "more", "label": ["Описание на большой карточке", "Description on a large card"]},
        {"key": "include", "type": "text", "default": "", "group": "more", "max": 200, "label": ["Только со словами", "Only with words"],
         "placeholder": ["через запятую", "comma-separated"]},
        {"key": "exclude", "type": "text", "default": "", "group": "more", "max": 200, "label": ["Без слов", "Without words"],
         "placeholder": ["через запятую", "comma-separated"]},
    ],
    "news": [
        {"key": "hours", "type": "choice", "default": "36", "style": "seg", "group": "main", "label": ["Свежесть", "Freshness"],
         "choices": [C("12", "12 ч", "12 h"), C("24", "Сутки", "Day"), C("36", "36 ч", "36 h"), C("72", "3 дня", "3 days"),
                     C("168", "Неделя", "Week")], "refetch": True},
        {"key": "summary", "type": "choice", "default": "auto", "style": "seg", "group": "main", "label": ["Пересказ", "Summary"],
         "choices": [C("auto", "На большой", "On large"), C("always", "Всегда", "Always"), C("never", "Нет", "Never")]},
        {"key": "out_lang", "type": "choice", "default": "ui", "style": "seg", "group": "more", "label": ["Язык пересказа", "Summary language"],
         "choices": [C("ui", "Как интерфейс", "Interface"), C("ru", "Русский", "Russian"), C("en", "English", "English")], "refetch": True},
        {"key": "sources", "type": "multi", "default": ["official", "media", "hn", "reddit", "search"], "group": "more",
         "label": ["Откуда брать", "Where to read"], "refetch": True,
         "choices": [C("official", "Официальные блоги и ленты", "Official blogs and feeds"),
                     C("media", "Профильные СМИ", "Specialised outlets"), C("hn", "Hacker News", "Hacker News"),
                     C("reddit", "Reddit", "Reddit"), C("search", "Веб-поиск", "Web search")]},
        {"key": "feeds", "type": "list", "default": [], "group": "more", "max_items": 6, "item_re": r"^https?://\S+$",
         "label": ["Свои ленты", "Your own feeds"], "placeholder": ["https://…/rss", "https://…/rss"], "refetch": True},
        {"key": "exclude", "type": "text", "default": "", "group": "more", "max": 200, "label": ["Исключить слова и сайты", "Exclude words and sites"],
         "placeholder": ["вебинар, example.com", "webinar, example.com"]},
        {"key": "show_meta", "type": "bool", "default": True, "group": "more", "label": ["Источник и время", "Source and time"]},
    ],
    "countdown": [
        {"key": "style", "type": "choice", "default": "cells", "style": "seg", "group": "main", "label": ["Вид", "Look"],
         "choices": [C("cells", "Ячейки", "Cells"), C("big", "Крупно", "Big"), C("progress", "Полоса", "Progress")]},
        {"key": "units", "type": "choice", "default": "auto", "style": "seg", "group": "main", "label": ["Точность", "Precision"],
         "choices": [C("auto", "Авто", "Auto"), C("days", "Дни", "Days"), C("dhm", "Д·Ч·М", "D·H·M"), C("dhms", "Д·Ч·М·С", "D·H·M·S")]},
        {"key": "show_date", "type": "bool", "default": True, "group": "more", "label": ["Дата события", "Event date"]},
        {"key": "after", "type": "choice", "default": "done", "style": "seg", "group": "more", "label": ["Когда наступит", "Once it's here"],
         "choices": [C("done", "«Наступило»", "“It's here”"), C("up", "Считать прошедшее", "Count up")]},
    ],
    "usage": [
        {"key": "windows", "type": "multi", "default": ["five_hour", "weekly", "other"], "group": "main", "choices": USAGE_WINDOWS,
         "label": ["Какие окна", "Which windows"]},
        {"key": "show_reset", "type": "bool", "default": True, "group": "more", "label": ["Время сброса", "Reset time"]},
        {"key": "show_pace", "type": "bool", "default": True, "group": "more", "label": ["Метка «сколько окна прошло»", "“Window elapsed” marker"]},
        {"key": "sort", "type": "choice", "default": "manual", "style": "seg", "group": "more", "label": ["Порядок аккаунтов", "Account order"],
         "choices": [C("manual", "Как добавлены", "As added"), C("fullest", "Сначала заполненные", "Fullest first")]},
    ],
    "todo": [
        {"key": "lists", "type": "lists", "default": [], "group": "main", "label": ["Списки", "Lists"],
         "hint": ["Можно показать несколько списков в одной панели.", "Show one or several lists in one panel."]},
        {"key": "filter", "type": "choice", "default": "all", "group": "main", "label": ["Показывать", "Show"],
         "choices": [C("all", "Все задачи", "All tasks"), C("today", "На сегодня и просроченные", "Today and overdue"),
                     C("week", "На 7 дней", "Next 7 days"), C("overdue", "Только просроченные", "Overdue only"),
                     C("nodate", "Без срока", "No due date"), C("starred", "Важные (приоритет 1–2)", "Important (priority 1–2)")]},
        {"key": "sort", "type": "choice", "default": "manual", "group": "main", "label": ["Сортировка", "Sort"],
         "choices": [C("manual", "Вручную", "Manual"), C("due", "По сроку", "By due date"), C("priority", "По приоритету", "By priority"),
                     C("created", "Новые сверху", "Newest first"), C("title", "По названию", "By title")]},
        {"key": "show_done", "type": "bool", "default": False, "group": "main", "label": ["Показывать выполненные", "Show completed"]},
        {"key": "quick_add", "type": "bool", "default": True, "group": "more", "label": ["Строка быстрого добавления", "Quick-add field"]},
        {"key": "tags", "type": "text", "default": "", "group": "more", "max": 120, "label": ["Только с тегами", "Only with tags"],
         "placeholder": ["работа, дом", "work, home"]},
        {"key": "show_due", "type": "bool", "default": True, "group": "more", "label": ["Сроки", "Due dates"]},
        {"key": "show_tags", "type": "bool", "default": True, "group": "more", "label": ["Теги", "Tags"]},
        {"key": "show_subtasks", "type": "bool", "default": True, "group": "more", "label": ["Подзадачи", "Subtasks"]},
        {"key": "group", "type": "choice", "default": "auto", "style": "seg", "group": "more", "label": ["Группировать", "Group"],
         "choices": [C("auto", "По спискам", "By list"), C("none", "Нет", "None"), C("due", "По сроку", "By due")]},
    ],
    "notes": [
        {"key": "font", "type": "choice", "default": "text", "style": "seg", "group": "main", "label": ["Шрифт", "Font"],
         "choices": [C("text", "Обычный", "Text"), C("mono", "Моноширинный", "Monospace")]},
        {"key": "text_size", "type": "choice", "default": "m", "style": "seg", "group": "main", "label": ["Размер текста", "Text size"],
         "choices": [C("s", "Мелкий", "Small"), C("m", "Обычный", "Normal"), C("l", "Крупный", "Large")]},
        {"key": "checklist", "type": "bool", "default": True, "group": "more", "label": ["Строки «[ ] …» — флажки", "Lines “[ ] …” are checkboxes"]},
    ],
    "clock": [
        {"key": "zones", "type": "list", "default": [], "group": "main", "max_items": 8, "item_re": r"^[A-Za-z_]+(?:/[A-Za-z0-9_+\-]+){0,2}(?:\|.{0,40})?$",
         "label": ["Часовые пояса", "Time zones"], "placeholder": ["Europe/London | Лондон", "Europe/London | London"],
         "hint": ["Пусто — только местное время. Подпись — после «|».", "Empty — local time only. A label goes after “|”."]},
        {"key": "local", "type": "bool", "default": True, "group": "main", "label": ["Местное время первым", "Local time first"]},
        {"key": "style", "type": "choice", "default": "digital", "style": "seg", "group": "main", "label": ["Вид", "Look"],
         "choices": [C("digital", "Цифры", "Digital"), C("analog", "Циферблат", "Analog")]},
        {"key": "h24", "type": "choice", "default": "auto", "style": "seg", "group": "more", "label": ["Формат", "Format"],
         "choices": [C("auto", "Как в системе", "System"), C("24", "24 ч", "24 h"), C("12", "12 ч", "12 h")]},
        {"key": "seconds", "type": "bool", "default": False, "group": "more", "label": ["Секунды", "Seconds"]},
        {"key": "date", "type": "bool", "default": True, "group": "more", "label": ["Дата", "Date"]},
        {"key": "diff", "type": "bool", "default": True, "group": "more", "label": ["Разница с местным", "Offset from local"]},
    ],
    "timers": [
        {"key": "parts", "type": "multi", "default": ["pomodoro", "timers", "stopwatch"], "group": "main", "choices": TIMER_PARTS,
         "label": ["Что показывать", "What to show"]},
        {"key": "controls", "type": "bool", "default": True, "group": "main", "label": ["Кнопки управления", "Controls"]},
        {"key": "stats", "type": "bool", "default": True, "group": "more", "label": ["Статистика помодоро", "Pomodoro stats"]},
    ],
    "proxy": [
        {"key": "period", "type": "choice", "default": "today", "style": "seg", "group": "main", "label": ["Период", "Period"],
         "choices": [C("today", "Сегодня", "Today"), C("day", "24 ч", "24 h"), C("week", "Неделя", "Week"), C("month", "Месяц", "Month")],
         "refetch": True},
        {"key": "fields", "type": "multi", "default": ["requests", "success", "tokens", "cost", "chart"], "group": "main",
         "choices": PROXY_FIELDS, "label": ["Что показывать", "What to show"]},
    ],
    "calendar": [
        {"key": "days", "type": "choice", "default": "3", "style": "seg", "group": "main", "label": ["На сколько дней", "Days ahead"],
         "choices": [C("1", "Сегодня", "Today"), C("3", "3 дня", "3 days"), C("7", "Неделя", "Week"), C("14", "2 недели", "2 weeks")],
         "refetch": True},
        {"key": "count", "type": "int", "default": 8, "min": 3, "max": 20, "group": "main", "label": ["Сколько событий", "How many events"],
         "refetch": True},
        {"key": "calendar", "type": "text", "default": "", "group": "more", "max": 200, "label": ["Календарь", "Calendar"],
         "placeholder": ["primary", "primary"], "hint": ["Пусто — основной календарь Google.", "Empty — the primary Google calendar."],
         "refetch": True},
        {"key": "show_location", "type": "bool", "default": True, "group": "more", "label": ["Место", "Location"]},
    ],
    "github": [
        {"key": "view", "type": "choice", "default": "notifications", "group": "main", "label": ["Что показывать", "What to show"],
         "choices": [C("notifications", "Уведомления", "Notifications"), C("review", "Ждут моего ревью", "Review requests"),
                     C("assigned", "Назначены мне (issues и PR)", "Assigned to me"), C("mine", "Мои открытые PR", "My open PRs")],
         "refetch": True},
        {"key": "count", "type": "int", "default": 8, "min": 3, "max": 20, "group": "main", "label": ["Сколько", "How many"], "refetch": True},
        {"key": "participating", "type": "bool", "default": False, "group": "more", "refetch": True,
         "label": ["Только где я участвую", "Only where I participate"], "when": {"view": ["notifications"]}},
    ],
}

for _kind in ("custom", "sports", "web"):
    SCHEMA.setdefault(_kind, [])

def defaults(kind: str) -> Dict[str, Any]:
    return {opt["key"]: (list(opt["default"]) if isinstance(opt["default"], list) else
                         dict(opt["default"]) if isinstance(opt["default"], dict) else opt["default"])
            for opt in SCHEMA.get(kind, [])}

def _choice_values(opt: Dict[str, Any]) -> List[str]:
    return [str(c[0]) for c in opt.get("choices") or []]

def clean_option(opt: Dict[str, Any], value: Any) -> Any:
    kind = opt["type"]
    default = opt["default"]
    if kind == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on", "да")
        return bool(value) if value is not None else default
    if kind == "choice":
        value = str(value if value is not None else "")
        return value if value in _choice_values(opt) else default
    if kind == "multi":
        if isinstance(value, str):
            value = [v.strip() for v in value.split(",")]
        if not isinstance(value, list):
            return list(default)
        allowed = _choice_values(opt)
        return list(dict.fromkeys(str(v) for v in value if str(v) in allowed))
    if kind == "int":
        try:
            number = int(float(value))
        except (TypeError, ValueError):
            return default
        return max(int(opt.get("min", number)), min(int(opt.get("max", number)), number))
    if kind == "text":
        return re.sub(r"[\x00-\x1f]", " ", str(value or "")).strip()[: int(opt.get("max") or 200)]
    if kind in ("list", "lists"):
        if isinstance(value, str):
            value = re.split(r"[,\n;]+", value)
        if not isinstance(value, list):
            return list(default)
        pattern = re.compile(opt.get("item_re") or r"^[a-z0-9_]{2,40}$")
        out = []
        for item in value:
            text = re.sub(r"\s*\|\s*", "|", str(item or "").strip())[:120]
            if text and pattern.match(text) and text not in out:
                out.append(text)
        return out[: int(opt.get("max_items") or 12)]
    if kind == "map":
        views = opt.get("views") or {}
        out = {}
        for key, view in (value.items() if isinstance(value, dict) else []):
            if key in views and (view in views[key]):
                out[key] = view
        return out
    return default

def clean_options(kind: str, raw: Any, *, base: Any = None) -> Dict[str, Any]:
    """Полный набор опций вида: значения из ``raw`` поверх ``base`` поверх умолчаний."""
    out = defaults(kind)
    for source in (base, raw):
        if not isinstance(source, dict):
            continue
        for opt in SCHEMA.get(kind, []):
            if opt["key"] in source:
                out[opt["key"]] = clean_option(opt, source[opt["key"]])
    return out

def needs_refetch(kind: str, before: Dict[str, Any], after: Dict[str, Any]) -> bool:
    return any(opt.get("refetch") and before.get(opt["key"]) != after.get(opt["key"]) for opt in SCHEMA.get(kind, []))

def public_schema() -> Dict[str, List[Dict[str, Any]]]:
    """Схема для окна (то же, что здесь: окно само выберет язык подписей)."""
    return SCHEMA

def system_view(options: Dict[str, Any], metric: str) -> str:
    """Каким видом рисовать метрику ПК: своя настройка метрики → общий «Вид» → первый допустимый."""
    allowed = SYSTEM_VIEWS.get(metric, ["number"])
    own = (options.get("views") or {}).get(metric)
    if own in allowed:
        return own
    style = options.get("style") or "rings"
    wanted = {"rings": "ring", "bars": "bar", "numbers": "number", "charts": "chart"}.get(style, "ring")
    if wanted in allowed:
        return wanted
    if wanted == "chart" and "bar" in allowed and metric not in ("temps",):
        return "bar" if "chart" not in allowed else "chart"
    if wanted == "ring" and "number" in allowed:
        return "number"
    return allowed[0]
