"""Fast local intent matcher for Quick Ask commands (Russian + English).

``match(text)`` turns a short imperative phrase («сделай громкость 30»,
«поставь таймер на 5 минут для чая», "remind me in telegram at 15:00 to call
mom") into ``{"action", "params", "confidence", "rule"}`` without any model
round trip. Anything it is not sure about returns ``None`` and goes to the
model, which has the same actions as tools. Rules are deliberately anchored
on the start of the phrase and questions ("как сделать громче?") are left to
the model.

Pure Python, no OS calls: fully unit-tested on Linux.
"""
from __future__ import annotations

import difflib
import re
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

_WAKE = re.compile(r"^(?:(?:эй|hey|ok|ок|окей)\s+)?(?:memu(?:ai)?|мему(?:аи)?|джарвис|jarvis)[,!:\s]+", re.I)
_POLITE = re.compile(r"\b(?:пожалуйста|плиз|плз|please|pls)\b[,]?\s*", re.I)
_POLITE_HEAD = re.compile(r"^(?:будь добр(?:а|ы)?|ты можешь|можешь|можно|could you|can you|would you|will you)\b[,]?\s+", re.I)

def normalize(text: Any) -> str:
    value = str(text or "").replace("ё", "е").replace("Ё", "Е").strip()
    value = _WAKE.sub("", value)
    value = _POLITE.sub("", value)
    value = _POLITE_HEAD.sub("", value)
    value = re.sub(r"\s+", " ", value).strip()

    value = re.sub(r"^(\w+)-ка\b", r"\1", value, flags=re.I)
    value = re.sub(r"^(\w+),\s+", r"\1 ", value)
    value = value.strip(" .!…")
    return value

def lower(text: str) -> str:
    return text.lower()

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z", "и": "i", "й": "y",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e",
    "ю": "yu", "я": "ya",
}

def translit(text: str) -> str:
    return "".join(_TRANSLIT.get(ch, ch) for ch in str(text or "").lower().replace("ё", "е"))

def fold(text: str) -> str:
    """Comparable key: lower, transliterated, letters and digits only."""
    return re.sub(r"[^a-z0-9]+", "", translit(text))

def similarity(a: str, b: str) -> float:
    fa, fb = fold(a), fold(b)
    if not fa or not fb:
        return 0.0
    if fa == fb:
        return 1.0
    return difflib.SequenceMatcher(None, fa, fb).ratio()

_UNITS_RU = {
    "ноль": 0, "один": 1, "одну": 1, "одна": 1, "одной": 1, "раз": 1, "два": 2, "две": 2, "три": 3,
    "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15,
    "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19,
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60, "семьдесят": 70,
    "восемьдесят": 80, "девяносто": 90, "сто": 100, "пару": 2, "пара": 2, "несколько": 3,
}
_UNITS_EN = {
    "zero": 0, "one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100, "couple": 2, "few": 3,
}
_NUMWORDS = {**_UNITS_RU, **_UNITS_EN}
_NUM_WORD_RE = "|".join(sorted((re.escape(w) for w in _NUMWORDS), key=len, reverse=True))
NUMBER = rf"(?:\d+(?:[.,]\d+)?|(?:(?:{_NUM_WORD_RE})(?:[\s-]+(?:{_NUM_WORD_RE}))?))"

def parse_number(text: str) -> Optional[float]:
    value = str(text or "").strip().lower().replace(",", ".")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    total = 0.0
    seen = False
    for word in re.split(r"[\s-]+", value):
        if word in ("a", "an") and seen:
            continue
        if word not in _NUMWORDS:
            return None
        total += _NUMWORDS[word]
        seen = True
    return total if seen else None

_UNIT_SECONDS = [
    (r"(?:час(?:а|ов)?|ч|hours?|hrs?|h)", 3600),
    (r"(?:минут(?:у|ы|ок|ки)?|мин|m|mins?|minutes?)", 60),
    (r"(?:секунд(?:у|ы)?|сек|s|secs?|seconds?)", 1),
]
_DUR_PART = re.compile(rf"(?P<num>{NUMBER})?\s*(?P<unit>{'|'.join(u for u, _ in _UNIT_SECONDS)})\b", re.I)
_SPECIAL_DUR = [
    (re.compile(r"\b(?:полчаса|пол часа|half an hour|half hour)\b", re.I), 1800),
    (re.compile(r"\b(?:полтора часа|полторы часа|an hour and a half|one and a half hours?)\b", re.I), 5400),
    (re.compile(r"\b(?:четверть часа|quarter of an hour|a quarter hour)\b", re.I), 900),
    (re.compile(r"\b(?:полминуты|пол минуты|half a minute)\b", re.I), 30),
    (re.compile(r"\b(?:полторы минуты|a minute and a half)\b", re.I), 90),
]

def _unit_seconds(unit: str) -> int:
    for pattern, seconds in _UNIT_SECONDS:
        if re.fullmatch(pattern, unit, re.I):
            return seconds
    return 0

def find_duration(text: str) -> Optional[Tuple[int, int, int]]:
    """``(seconds, start, end)`` of the first duration phrase in ``text`` («5 минут 30 секунд», "1h 20m")."""
    for pattern, seconds in _SPECIAL_DUR:
        found = pattern.search(text)
        if found:
            return seconds, found.start(), found.end()
    clock = re.search(r"\b(\d{1,2}):(\d{2})(?::(\d{2}))?\b", text)
    best: Optional[Tuple[int, int, int]] = None
    matches = list(_DUR_PART.finditer(text))
    i = 0
    while i < len(matches):
        first = matches[i]
        num_text = first.group("num")
        unit = first.group("unit")

        if num_text is None and len(unit) <= 2:
            i += 1
            continue
        number = parse_number(num_text) if num_text else 1.0
        if number is None:
            i += 1
            continue
        total = number * _unit_seconds(unit)
        start, end = first.start(), first.end()
        j = i + 1
        while j < len(matches):
            nxt = matches[j]
            gap = text[end:nxt.start()]
            if not re.fullmatch(r"\s*(?:и|and|,)?\s*", gap, re.I) or nxt.group("num") is None:
                break
            value = parse_number(nxt.group("num"))
            if value is None:
                break
            total += value * _unit_seconds(nxt.group("unit"))
            end = nxt.end()
            j += 1
        if total > 0:
            best = (int(round(total)), start, end)
            break
        i += 1
    if best is None and clock and re.search(r"(?:таймер|timer|на|for)\s*$", text[:clock.start()], re.I):
        h, m, s = int(clock.group(1)), int(clock.group(2)), int(clock.group(3) or 0)

        total = h * 3600 + m * 60 + s if clock.group(3) else h * 60 + m
        best = (total, clock.start(), clock.end())
    return best

def duration_text(seconds: int, lang: str = "ru") -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if lang == "en":
        parts = [f"{h} h" if h else "", f"{m} min" if m else "", f"{s} s" if s else ""]
    else:
        parts = [f"{h} ч" if h else "", f"{m} мин" if m else "", f"{s} с" if s else ""]
    return " ".join(p for p in parts if p) or ("0 s" if lang == "en" else "0 с")

_DAY_WORDS = {"сегодня": 0, "today": 0, "завтра": 1, "tomorrow": 1, "послезавтра": 2,
              "day after tomorrow": 2}
_CLOCK = re.compile(
    r"(?:(?P<day>послезавтра|сегодня|завтра|day after tomorrow|today|tomorrow)\s*)?"
    r"(?:(?:в|к|на|at|by|for)\s+)?"
    r"(?P<h>\d{1,2})(?:[:.](?P<m>\d{2}))?\s*"
    r"(?P<suffix>am|pm|a\.m\.|p\.m\.|утра|вечера|дня|ночи|час(?:а|ов)?(?:\s+(?:утра|вечера|дня|ночи))?|o'?clock)?"
    r"(?:\s*(?P<day2>послезавтра|сегодня|завтра|tomorrow|today))?", re.I)

def find_clock(text: str, now: datetime, *, pm_guess: bool = True) -> Optional[Tuple[datetime, int, int]]:
    """``(datetime, start, end)`` of a clock time («в 15:00», «завтра в 9 утра», "at 3pm")."""
    for found in _CLOCK.finditer(text):
        hour = int(found.group("h"))
        minute = int(found.group("m") or 0)
        suffix = (found.group("suffix") or "").lower().replace(".", "")
        day_word = (found.group("day") or found.group("day2") or "").lower()
        has_prep = bool(re.match(r"(?:(?:послезавтра|сегодня|завтра|day after tomorrow|today|tomorrow)\s*)?"
                                 r"(?:в|к|на|at|by|for)\s", found.group(0), re.I))

        if not (found.group("m") or has_prep or day_word or suffix):
            continue
        if found.group("m") is None and not suffix and not day_word and not re.search(
                r"(?:в|к|at|by)\s+\d", found.group(0), re.I):
            continue

        if re.match(r"(?:на|for)\s", found.group(0), re.I) and not (found.group("m") or suffix):
            continue
        if suffix in ("pm", "вечера", "дня") or "вечера" in suffix or "дня" in suffix:
            if hour < 12:
                hour += 12
        elif suffix in ("am", "утра", "ночи") or "утра" in suffix or "ночи" in suffix:
            if hour == 12:
                hour = 0
        if hour > 23 or minute > 59:
            continue
        offset = _DAY_WORDS.get(day_word)
        base = now.replace(second=0, microsecond=0)
        when = base.replace(hour=hour, minute=minute)
        if offset is not None:
            when = when + timedelta(days=offset)

            if offset == 0 and when <= now and pm_guess and hour < 12 and not suffix and found.group("m") is None \
                    and (when + timedelta(hours=12)) > now:
                when = when + timedelta(hours=12)
        elif when <= now:

            if pm_guess and hour < 12 and not suffix and found.group("m") is None and (when + timedelta(hours=12)) > now:
                when = when + timedelta(hours=12)
            else:
                when = when + timedelta(days=1)
        return when, found.start(), found.end()
    return None

_WEEKDAYS = [
    (r"понедельник|monday", 0), (r"вторник|tuesday", 1), (r"среду|wednesday", 2),
    (r"четверг|thursday", 3), (r"пятницу|friday", 4), (r"субботу|saturday", 5),
    (r"воскресенье|sunday", 6),
]

_WEEKDAY = re.compile(
    r"(?:(?<=\s)|^)(?:в|во|on|this|next|в эт[уо]т?|в эту|в следующ\w+|в ближайш\w+)\s+(?P<wd>"
    + "|".join(f"(?:{pattern})" for pattern, _ in _WEEKDAYS) + r")\b", re.I)

def find_weekday(text: str, now: datetime) -> Optional[Tuple[datetime, int, int]]:
    """``(date at 00:00, start, end)`` of «в среду» / "on Friday" — the nearest such day (today counts)."""
    found = _WEEKDAY.search(text)
    if not found:
        return None
    word = found.group("wd").lower()
    target = next(day for pattern, day in _WEEKDAYS if re.fullmatch(pattern, word, re.I))
    offset = (target - now.weekday()) % 7
    if re.search(r"следующ|next", found.group(0), re.I) and offset == 0:
        offset = 7
    day = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=offset)
    return day, found.start(), found.end()

FOOD_WORDS = ("ролл", "суши", "пицц", "бургер", "шаурм", "шаверм", "вок", "лапш", "еду", "еда", "обед",
              "ужин", "завтрак", "кофе", "пончик", "салат", "суп", "стейк", "хинкал", "пельмен", "плов",
              "шашлык", "фастфуд", "макдак", "kfc", "продукт", "доставк", "roll", "sushi", "pizza", "burger",
              "food", "lunch", "dinner", "breakfast", "coffee", "noodle", "tacos", "taco", "kebab", "salad",
              "ramen", "groceries", "chinese", "thai", "poke", "pho")

KNOWN_SITES = {
    "youtube": "https://www.youtube.com", "ютуб": "https://www.youtube.com", "ютюб": "https://www.youtube.com",
    "google": "https://www.google.com", "гугл": "https://www.google.com", "gmail": "https://mail.google.com",
    "почту": "https://mail.google.com", "почта": "https://mail.google.com", "джимейл": "https://mail.google.com",
    "яндекс почту": "https://mail.yandex.ru", "яндекс почта": "https://mail.yandex.ru",
    "vk": "https://vk.com", "вк": "https://vk.com", "вконтакте": "https://vk.com",
    "github": "https://github.com", "гитхаб": "https://github.com", "chatgpt": "https://chatgpt.com",
    "википедию": "https://ru.wikipedia.org", "wikipedia": "https://en.wikipedia.org",
    "twitch": "https://www.twitch.tv", "твич": "https://www.twitch.tv", "reddit": "https://www.reddit.com",
    "реддит": "https://www.reddit.com", "x": "https://x.com", "twitter": "https://x.com", "твиттер": "https://x.com",
    "instagram": "https://www.instagram.com", "инстаграм": "https://www.instagram.com",
    "кинопоиск": "https://www.kinopoisk.ru", "netflix": "https://www.netflix.com", "нетфликс": "https://www.netflix.com",
    "госуслуги": "https://www.gosuslugi.ru", "озон": "https://www.ozon.ru", "ozon": "https://www.ozon.ru",
    "вайлдберриз": "https://www.wildberries.ru", "wildberries": "https://www.wildberries.ru",
    "авито": "https://www.avito.ru", "avito": "https://www.avito.ru", "amazon": "https://www.amazon.com",
    "яндекс": "https://ya.ru", "переводчик": "https://translate.yandex.ru", "google translate": "https://translate.google.com",
    "яндекс карты": "https://yandex.ru/maps", "google maps": "https://maps.google.com", "карты": "https://yandex.ru/maps",
    "stackoverflow": "https://stackoverflow.com", "habr": "https://habr.com", "хабр": "https://habr.com",
}

SETTINGS_WORDS = [
    (r"звук\w*|sound|audio", "sound"), (r"экран\w*|диспле\w*|display|screen|монитор\w*", "display"),
    (r"bluetooth|блютуз\w*|блютус\w*", "bluetooth"), (r"wi-?fi|вай-?фай\w*|вайфай\w*", "wifi"),
    (r"сет[иь]\w*|интернет\w*|network", "network"), (r"обновлени\w*|windows update|update", "update"),
    (r"приложени\w*|программ\w*|apps", "apps"), (r"автозагрузк\w*|startup", "startup"),
    (r"батаре\w*|аккумулятор\w*|battery", "battery"), (r"питани\w*|power|сна|sleep", "power"),
    (r"мыш\w*|тачпад\w*|mouse|touchpad", "mouse"), (r"клавиатур\w*|keyboard", "keyboard"),
    (r"персонализаци\w*|personali[sz]ation", "personalization"), (r"фон\w*|обо[ий]\w*|wallpaper|background", "background"),
    (r"цвет\w*|colou?rs?", "colors"), (r"тем\w*|themes?", "themes"), (r"панел\w* задач|taskbar", "taskbar"),
    (r"уведомлени\w*|notifications?", "notifications"), (r"конфиденциальност\w*|privacy", "privacy"),
    (r"камер\w*|camera|webcam", "camera"), (r"микрофон\w*|microphone|mic", "microphone"),
    (r"принтер\w*|printers?", "printers"), (r"дат\w*|времен\w*|date|time", "datetime"),
    (r"язык\w*|language|region", "language"), (r"хранилищ\w*|памят\w*|диск\w*|storage", "storage"),
    (r"о системе|about", "about"), (r"ночн\w* свет\w*|night ?light", "nightlight"), (r"vpn|впн", "vpn"),
    (r"режим\w* в самол[её]те|airplane", "airplane"), (r"буфер\w*|clipboard", "clipboard"),
    (r"специальн\w* возможност\w*|accessibility", "accessibility"), (r"по умолчанию|default apps", "default_apps"),
]

MEMUAI_SECTIONS = [
    (r"голос\w*|voice", "voice"), (r"модел\w*|models?", "models"), (r"оформлени\w*|внешн\w* вид|appearance|тем\w*", "appearance"),
    (r"панел\w*|panels?", "panels"), (r"модул\w*|modules?|студи\w*|studio", "modules"),
    (r"интеграци\w*|integrations?", "integrations"), (r"промпт\w*|prompts?", "prompts"),
    (r"телеграм\w*|telegram|удал[её]нн\w*|remote", "remote"), (r"work|ворк|проект\w*", "work"),
    (r"документаци\w*|справк\w*|docs?|help", "docs"), (r"быстр\w* доступ\w*|quick( ask)?", "quick_settings"),
    (r"о программе|about", "about"), (r"приложени\w*|apps", "apps"),
]

FOLDER_WORDS = [
    (r"загрузк\w*|downloads?", "downloads"), (r"документ\w*|documents?|docs", "documents"),
    (r"рабоч\w* стол\w*|desktop", "desktop"), (r"изображени\w*|картинк\w*|фото\w*|pictures?|photos?", "pictures"),
    (r"музык\w*|music", "music"), (r"видео|videos?", "videos"), (r"скриншот\w*|screenshots?", "screenshots"),
    (r"appdata|апдат\w*", "appdata"), (r"temp|темп|временн\w*", "temp"), (r"домашн\w*|home|профил\w*", "home"),
]

Rule = Tuple[str, "re.Pattern[str]", Callable[["re.Match[str]", "Ctx"], Optional[Tuple[str, Dict[str, Any]]]]]

class Ctx:
    def __init__(self, text: str, now: datetime) -> None:
        self.text = text
        self.low = text.lower()
        self.now = now

_RULES: List[Rule] = []

def rule(name: str, pattern: str, flags: int = re.I):
    compiled = re.compile(pattern, flags)

    def wrap(fn):
        _RULES.append((name, compiled, fn))
        return fn
    return wrap

def _const(act: str, params: Dict[str, Any]):
    return lambda m, c: (act, dict(params))

def add(name: str, pattern: str, act: str, /, **params: Any) -> None:
    _RULES.append((name, re.compile(pattern, re.I), _const(act, params)))

PC = r"(?:компьютер|комп|пк|ноутбук|ноут|систему|windows|винду|the computer|computer|the pc|pc|laptop|my computer|my pc)"

@rule("power.delayed", rf"^(?P<verb>выключи|выруби|отключи|перезагрузи|перезапусти|shut down|shutdown|power off|turn off|"
      rf"switch off|restart|reboot)\s+{PC}\s+(?P<rest>(?:через|in|в|at|к|by)\s.+)$")
def _power_delayed(m, c):
    """«выключи компьютер через час», «перезагрузи пк в 23:00» → a delayed shutdown (cancellable)."""
    rest = m.group("rest")
    seconds: Optional[float] = None
    if re.match(r"(?:через|in)\s", rest, re.I):
        dur = find_duration(rest)
        if dur and not rest[dur[2]:].strip():
            seconds = dur[0]
    else:
        clock = find_clock(rest, c.now)
        if clock and not rest[clock[2]:].strip():
            seconds = (clock[0] - c.now).total_seconds()
    if seconds is None or seconds <= 0:
        return None
    action = "power.restart" if re.match(r"перезагрузи|перезапусти|restart|reboot", m.group("verb"), re.I) else "power.shutdown"
    return action, {"delay_minutes": max(1, int(round(seconds / 60)))}

add("power.cancel", r"^(?:отмени|останови|cancel|abort|stop)\s+(?:выключение|перезагрузку|shutdown|restart|reboot)(?: компьютера| пк)?$", "power.cancel")
add("power.shutdown", rf"^(?:выключи|выруби|отключи|shut down|shutdown|power off|turn off|switch off)\s+{PC}$", "power.shutdown")
add("power.shutdown2", r"^(?:заверши работу(?: компьютера| windows| пк)?|выключение компьютера|shut ?down|power off)$", "power.shutdown")
add("power.restart", rf"^(?:перезагрузи(?:сь)?|перезапусти|рестарт|перезагрузка|restart|reboot)(?:\s+{PC})?$", "power.restart")
add("power.sleep", rf"^(?:(?:отправь|переведи|усыпи)\s+{PC}\s+в\s+(?:сон|спящий режим)|спящий режим|режим сна|усыпи(?:\s+{PC})?|"
    rf"в сон|go to sleep|sleep(?: mode)?|put (?:the )?(?:computer|pc|laptop) to sleep)$", "power.sleep")
add("power.hibernate", rf"^(?:гибернаци\w*|в гибернацию|hibernate(?:\s+{PC})?)$", "power.hibernate")
add("lock.screen", rf"^(?:заблокируй|заблокировать|блокировка|lock)(?:\s+(?:экран|{PC}|the screen|screen|workstation))?$", "lock.screen")
add("power.signout", r"^(?:выйди из (?:системы|учетной записи|аккаунта windows)|выход из системы|sign out|log ?out|log ?off)$", "power.signout")

@rule("volume.set", rf"^(?:(?:сделай|поставь|установи|выстави|set|make|change)\s+)?(?:(?:the|master)\s+)?"
      rf"(?:громкость|звук|volume)\s+(?:на\s+|в\s+|до\s+|to\s+|at\s+)?(?P<n>{NUMBER})\s*(?:%|процент\w*|percent)?$")
def _volume_set(m, c):
    value = parse_number(m.group("n"))
    return ("volume.set", {"level": value}) if value is not None and 0 <= value <= 100 else None

@rule("volume.up", rf"^(?:(?:сделай|давай)\s+)?(?:погромче|громче|прибавь(?: звук| громкость)?|увеличь (?:звук|громкость)|"
      rf"louder|make it louder|volume up|turn (?:it |the volume )?up|turn up the (?:volume|sound)|increase (?:the )?volume)(?:\s+(?:на|by)\s+(?P<n>{NUMBER})\s*(?:%|процент\w*|percent)?)?$")
def _volume_up(m, c):
    value = parse_number(m.group("n")) if m.group("n") else 10
    return ("volume.set", {"delta": value}) if value is not None else None

@rule("volume.down", rf"^(?:(?:сделай|давай)\s+)?(?:потише|тише|убавь(?: звук| громкость)?|уменьши (?:звук|громкость)|"
      rf"quieter|make it quieter|volume down|turn (?:it |the volume )?down|turn down the (?:volume|sound)|decrease (?:the )?volume|lower (?:the )?volume)"
      rf"(?:\s+(?:на|by)\s+(?P<n>{NUMBER})\s*(?:%|процент\w*|percent)?)?$")
def _volume_down(m, c):
    value = parse_number(m.group("n")) if m.group("n") else 10
    return ("volume.set", {"delta": -value}) if value is not None else None

add("volume.max", r"^(?:(?:сделай|поставь)\s+)?(?:громкость|звук)\s+(?:на\s+)?(?:максимум|максимальн\w*|полную)$|^(?:max(?:imum)? volume|volume (?:to )?max(?:imum)?)$",
    "volume.set", level=100)
add("volume.min", r"^(?:(?:сделай|поставь)\s+)?(?:громкость|звук)\s+(?:на\s+)?(?:минимум|минимальн\w*)$|^(?:min(?:imum)? volume|volume (?:to )?min(?:imum)?)$",
    "volume.set", level=0)
add("volume.mute", r"^(?:выключи звук|отключи звук|убери звук|без звука|замьють|мьют|mute(?: (?:the )?(?:sound|audio|volume))?|"
    r"заглуши(?: звук)?)$", "volume.mute", mute=True)
add("volume.unmute", r"^(?:включи звук|верни звук|размьють|unmute(?: (?:the )?(?:sound|audio))?)$", "volume.mute", mute=False)
add("volume.get", r"^(?:какая (?:сейчас )?громкость|громкость|сколько громкость|what(?:'s| is) the volume|volume)\??$", "volume.get")

add("media.pause", r"^(?:пауза|поставь на паузу|на паузу|останови музыку|стоп музыка|pause(?: (?:the )?(?:music|video|playback))?)$",
    "media.key", key="play_pause")
add("media.play", r"^(?:продолжи(?: воспроизведение| музыку)?|воспроизведи|play|resume(?: (?:the )?(?:music|playback))?|"
    r"сними с паузы|играй)$", "media.key", key="play_pause")
add("media.next", r"^(?:следующ\w+(?: трек| песн\w*| видео)?|дальше|некст|переключи трек|skip(?: (?:the )?(?:track|song))?|"
    r"next(?: (?:track|song))?)$", "media.key", key="next")
add("media.prev", r"^(?:предыдущ\w+(?: трек| песн\w*| видео)?|прошл\w+ трек|previous(?: (?:track|song))?|prev)$",
    "media.key", key="prev")

@rule("brightness.set", rf"^(?:(?:сделай|поставь|установи|set|make|change)\s+)?(?:(?:the|screen)\s+)?(?:яркость|brightness)"
      rf"(?:\s+экрана)?\s+(?:на\s+|в\s+|до\s+|to\s+|at\s+)?(?P<n>{NUMBER})\s*(?:%|процент\w*|percent)?$")
def _brightness_set(m, c):
    value = parse_number(m.group("n"))
    return ("brightness.set", {"level": value}) if value is not None and 0 <= value <= 100 else None

@rule("brightness.up", rf"^(?:(?:сделай|сделать)\s+)?(?:экран\s+)?(?:ярче|поярче|прибавь яркость|увеличь яркость|brighter|"
      rf"brightness up|increase (?:the )?brightness)(?:\s+(?:на|by)\s+(?P<n>{NUMBER})\s*%?)?$")
def _brightness_up(m, c):
    value = parse_number(m.group("n")) if m.group("n") else 15
    return ("brightness.set", {"delta": value}) if value is not None else None

@rule("brightness.down", rf"^(?:(?:сделай|сделать)\s+)?(?:экран\s+)?(?:темнее|потемнее|тусклее|убавь яркость|уменьши яркость|dimmer|"
      rf"brightness down|decrease (?:the )?brightness|dim (?:the )?screen)(?:\s+(?:на|by)\s+(?P<n>{NUMBER})\s*%?)?$")
def _brightness_down(m, c):
    value = parse_number(m.group("n")) if m.group("n") else 15
    return ("brightness.set", {"delta": -value}) if value is not None else None

add("brightness.get", r"^(?:какая (?:сейчас )?яркость|яркость|what(?:'s| is) the brightness|brightness)\??$", "brightness.get")

add("theme.dark", r"^(?:(?:включи|поставь|сделай|переключи на)\s+)?(?:темн\w+ (?:тем\w+|режим)|ночн\w+ тем\w+|dark (?:mode|theme)|"
    r"(?:turn on|enable|switch to) dark (?:mode|theme))(?: windows)?$", "theme.set", mode="dark")
add("theme.light", r"^(?:(?:включи|поставь|сделай|переключи на)\s+)?(?:светл\w+ (?:тем\w+|режим)|light (?:mode|theme)|"
    r"(?:turn on|enable|switch to) light (?:mode|theme))(?: windows)?$", "theme.set", mode="light")
add("theme.toggle", r"^(?:переключи|смени|поменяй) (?:тем\w+|режим)(?: windows)?$|^toggle (?:the )?(?:theme|dark mode)$",
    "theme.set", mode="toggle")
add("nightlight", r"^(?:(?:включи|выключи|открой)\s+)?(?:ночн\w+ свет|ночн\w+ режим|night ?light)$", "nightlight.open")

@rule("wifi", r"^(?P<verb>включи|выключи|отключи|вруби|выруби|turn on|turn off|enable|disable)\s+(?:the\s+)?"
      r"(?:wi-?fi|вай-?фай|вайфай|wifi|wlan)$")
def _wifi(m, c):
    return "wifi.set", {"on": m.group("verb").lower() in ("включи", "вруби", "turn on", "enable")}

@rule("bluetooth", r"^(?P<verb>включи|выключи|отключи|вруби|выруби|turn on|turn off|enable|disable)\s+(?:the\s+)?"
      r"(?:bluetooth|блютуз|блютус|блютуc)$")
def _bt(m, c):
    return "bluetooth.set", {"on": m.group("verb").lower() in ("включи", "вруби", "turn on", "enable")}

_TIMER_HEAD = r"^(?:(?:поставь|заведи|запусти|включи|засеки|установи|сделай|start|set|start a|set a|make a)\s+)?(?:(?:a|the)\s+)?"

def _label_from(rest: str) -> str:

    rest = re.sub(r"^\s*(?:,|—|-|:)?\s*(?:на|под|про|чтобы|for|called|named|labelled|labeled)\s+", "", rest.strip(), flags=re.I)
    rest = rest.strip(" ,.:—-\"'«»")
    return rest[:60]

@rule("timer.start", _TIMER_HEAD + r"(?:таймер|timer)\b(?P<rest>.*)$")
def _timer(m, c):
    rest = m.group("rest")
    bare = re.fullmatch(r"\s*(?:на|for)?\s*(\d{1,4})\s*", rest, re.I)
    if bare and 0 < int(bare.group(1)) <= 1440:
        return "timer.start", {"seconds": int(bare.group(1)) * 60}
    found = find_duration(rest)
    if not found:
        return None
    seconds, start, end = found
    label = _label_from(rest[:start].replace(" на", " ").replace(" for", " ") + " " + rest[end:])
    label = re.sub(r"^(?:на|for)\b\s*", "", label, flags=re.I).strip()
    return "timer.start", {"seconds": seconds, **({"label": label} if label else {})}

@rule("timer.start2", r"^(?:засеки|отсчитай|count down|countdown)\s+(?P<rest>.+)$")
def _timer2(m, c):
    found = find_duration(m.group("rest"))
    if not found:
        return None
    seconds, start, end = found
    label = _label_from(m.group("rest")[end:])
    return "timer.start", {"seconds": seconds, **({"label": label} if label else {})}

@rule("timer.start3", rf"^(?P<dur>(?:{NUMBER}\s*)?(?:минут\w*|мин|час\w*|секунд\w*|minutes?|hours?|seconds?|mins?)(?:\s+\d+\s*\w+)?)\s+(?:таймер|timer)(?P<rest>.*)$")
def _timer3(m, c):
    found = find_duration(m.group("dur"))
    if not found:
        return None
    label = _label_from(m.group("rest"))
    return "timer.start", {"seconds": found[0], **({"label": label} if label else {})}

@rule("timer.cancel", r"^(?:отмени|останови|убери|сбрось|выключи|удали|cancel|stop|delete|clear)\s+(?:все\s+|all\s+)?(?:the\s+)?"
      r"(?:таймер\w*|timers?)(?P<rest>.*)$")
def _timer_cancel(m, c):
    label = _label_from(m.group("rest"))
    if re.search(r"\b(?:все|all)\b", c.low) or not label:
        return ("timer.cancel", {"all": True}) if re.search(r"\b(?:все|all)\b|таймеры|timers", c.low) else ("timer.cancel", {})
    return "timer.cancel", {"label": label}

add("timer.list", r"^(?:сколько (?:времени )?осталось(?: на таймере)?|покажи таймеры|мои таймеры|таймеры|какие (?:есть )?таймеры|timers|"
    r"show (?:my )?timers|how much time (?:is )?left|time left)\??$", "timer.list")
add("stopwatch.start", r"^(?:(?:запусти|включи|старт|начни|start)\s+)?(?:секундомер|stopwatch)$", "stopwatch.control", action="start")
add("stopwatch.stop", r"^(?:останови|стоп|выключи|stop|pause)\s+(?:секундомер|stopwatch)$", "stopwatch.control", action="stop")
add("stopwatch.reset", r"^(?:сбрось|обнули|reset)\s+(?:секундомер|stopwatch)$", "stopwatch.control", action="reset")
add("stopwatch.lap", r"^(?:круг|отметь круг|lap)$", "stopwatch.control", action="lap")

@rule("alarm.set", r"^(?:(?:поставь|заведи|установи|set|set an?)\s+)?(?:будильник|alarm)\s+(?P<rest>.+)$|"
      r"^(?:разбуди(?: меня)?|wake me(?: up)?)\s+(?P<rest2>.+)$")
def _alarm(m, c):
    rest = m.group("rest") or m.group("rest2") or ""
    found = find_clock(rest, c.now, pm_guess=False)
    bare = re.fullmatch(r"(?:на|в|к|at|for)\s+(\d{1,2})(?:\s+(?:час\w*|o'?clock))?", rest.strip(), re.I)
    if not found and bare and int(bare.group(1)) < 24:

        found = find_clock(f"в {int(bare.group(1))}:00", c.now, pm_guess=False)
        if found:
            found = (found[0], 0, len(rest))
    if not found:
        dur = find_duration(rest)
        if dur and re.search(r"\b(?:через|in)\b", rest, re.I):
            when = c.now + timedelta(seconds=dur[0])
            return "alarm.set", {"time": when.strftime("%H:%M"), "date": when.strftime("%Y-%m-%d")}
        return None
    when, start, end = found
    label = _label_from(rest[end:])
    params = {"time": when.strftime("%H:%M"), "date": when.strftime("%Y-%m-%d")}
    if label:
        params["label"] = label
    return "alarm.set", params

@rule("pomodoro", r"^(?P<verb>(?:запусти|начни|старт|включи|start|begin|run)\s+)?(?:помодоро|pomodoro|помидор\w*|фокус-сесси\w*|focus session)"
      rf"(?:\s+(?:на|for)\s+(?P<n>{NUMBER})\s*(?:минут\w*|мин|minutes?|mins?))?$")
def _pomodoro(m, c):
    params: Dict[str, Any] = {"action": "start"}
    if m.group("n"):
        value = parse_number(m.group("n"))
        if value:
            params["work"] = int(value)
    return "pomodoro.control", params

@rule("pomodoro.ctl", r"^(?P<verb>останови|стоп|выключи|заверши|пауза|поставь на паузу|продолжи|пропусти|stop|end|pause|resume|skip)\s+"
      r"(?:помодоро|pomodoro|помидор\w*|фокус-сесси\w*|focus session)$")
def _pomodoro_ctl(m, c):
    verb = m.group("verb").lower()
    action = {"останови": "stop", "стоп": "stop", "выключи": "stop", "заверши": "stop", "stop": "stop", "end": "stop",
              "пауза": "pause", "поставь на паузу": "pause", "pause": "pause", "продолжи": "resume",
              "resume": "resume", "пропусти": "skip", "skip": "skip"}[verb]
    return "pomodoro.control", {"action": action}

add("pomodoro.stats", r"^(?:статистика (?:помодоро|pomodoro)|сколько помидоров\w*|pomodoro stats)\??$", "pomodoro.control", action="stats")

_TG = re.compile(r"\s*\b(?:в телеграм(?:м)?е?|в телеге|в тг|телеграм(?:м)?ом|в telegram|через телеграм|"
                 r"(?:in|via|on|through) telegram|to telegram|telegram)\b", re.I)
_LOCAL_AND_TG = re.compile(r"\b(?:и здесь и в телеграм\w*|и в телеграм\w*|везде|both|everywhere)\b", re.I)

_RECURRING = re.compile(r"\b(?:каждый|каждую|каждое|каждые|ежедневно|еженедельно|ежемесячно|по будням|по выходным|"
                        r"по (?:понедельникам|вторникам|средам|четвергам|пятницам|субботам|воскресеньям)|"
                        r"every|each day|daily|weekly|monthly|on weekdays|on weekends)\b", re.I)

@rule("reminder", r"^(?:напомни(?:те)?|напоминание|remind me|reminder|set a reminder)\b(?P<rest>.*)$")
def _reminder(m, c):
    rest = " " + m.group("rest").strip() + " "
    if _RECURRING.search(rest):
        return None
    channel = "local"
    if _LOCAL_AND_TG.search(rest):
        channel = "both"
        rest = _LOCAL_AND_TG.sub(" ", rest)
    elif _TG.search(rest):
        channel = "telegram"
    rest = _TG.sub(" ", rest)
    rest = re.sub(r"^\s*(?:мне|me)\b", " ", rest, flags=re.I)
    params: Dict[str, Any] = {"channel": channel}
    rel = re.search(r"\b(?:через|in)\s+(.+?)(?=\s+(?:о|об|про|что|чтобы|to|about|that)\b|$)", rest, re.I)
    when_span: Optional[Tuple[int, int]] = None
    if rel:
        dur = find_duration(rel.group(1))
        if dur:
            seconds, s0, s1 = dur
            params["in_minutes"] = round(seconds / 60, 2)
            when_span = (rel.start(), rel.start(1) + s1)
    if when_span is None:
        weekday = find_weekday(rest, c.now)
        if weekday:

            day, w0, w1 = weekday
            rest = rest[:w0] + " " + rest[w1:]
            clock = find_clock(rest, c.now, pm_guess=False)
            if not clock:
                return None
            when = day.replace(hour=clock[0].hour, minute=clock[0].minute)
            if when <= c.now:
                when += timedelta(days=7)
            s0, s1 = clock[1], clock[2]
        else:
            clock = find_clock(rest, c.now)
            if not clock:
                return None
            when, s0, s1 = clock
        params["at"] = when.strftime("%Y-%m-%dT%H:%M")
        when_span = (s0, s1)
    text = (rest[:when_span[0]] + " " + rest[when_span[1]:])
    text = re.sub(r"\s+", " ", text).strip(" ,.:—-")
    text = re.sub(r"^(?:о|об|про|что|чтобы|to|about|that)\s+", "", text, flags=re.I).strip(" ,.:—-")
    if not text:
        return None
    params["text"] = text[:500]
    return "reminder.create", params

add("reminder.list", r"^(?:мои напоминания|покажи напоминания|какие (?:у меня )?напоминания|напоминания|my reminders|show reminders|reminders)\??$",
    "reminder.list")

_PLAN_DAYS = (r"(?:каждый|каждую|каждое|каждые|ежедневно|еженедельно|ежемесячно|по будням|по выходным|"
              r"по (?:понедельникам|вторникам|средам|четвергам|пятницам|субботам|воскресеньям)|"
              r"every|each|daily|weekly|monthly|on weekdays|on weekends)")

@rule("schedule.plan", r"^(?:(?P<verb>запланируй(?:те)?|спланируй(?:те)?|создай (?:задачу|расписание)|поставь задачу|"
      r"schedule|plan a task|create a (?:scheduled )?task)\b[\s:,]*(?P<rest>.*)|"
      r"(?P<repeat>(?:напоминай(?:те)?|присылай(?:те)?|отправляй(?:те)?|проверяй(?:те)?|собирай(?:те)?|"
      r"keep reminding me|remind me every|send me every)\b.*)|"
      rf"(?P<remind>(?:напомни(?:те)?|remind me)\b.*\b{_PLAN_DAYS}\b.*)|"
      rf"(?P<days>{_PLAN_DAYS}\b.{{3,}}))$")
def _schedule_plan(m, c):
    text = m.group(0).strip()
    if text.endswith("?"):
        return None
    if m.group("verb") is not None and len(m.group("rest").strip()) < 3:
        return None
    return "schedule.plan", {"text": text[:500]}

add("screenshot.region", r"^(?:(?:сделай|сними)\s+)?(?:скриншот|снимок|скрин)\s+(?:области|части экрана|фрагмента)$|^(?:take a )?(?:region|area) screenshot$|^snip$",
    "screenshot.take", mode="region")
add("screenshot.window", r"^(?:(?:сделай|сними)\s+)?(?:скриншот|снимок|скрин)\s+(?:окна|активного окна)$|^(?:take a )?window screenshot$",
    "screenshot.take", mode="window")
add("screenshot", r"^(?:(?:сделай|сними)\s+)?(?:скриншот|снимок экрана|скрин)(?:\s+экрана)?$|^(?:take a |make a )?screenshot$|^screen ?shot$",
    "screenshot.take", mode="screen")
add("clipboard.get", r"^(?:что (?:у меня )?в буфере(?: обмена)?|покажи буфер(?: обмена)?|what(?:'s| is) (?:in )?(?:my |the )?clipboard)\??$",
    "clipboard.get")
@rule("clipboard.put", r"^(?:скопируй|copy)\s+(?P<text>.+?)\s+(?:в буфер(?: обмена)?|to (?:the )?clipboard)$")
def _clip_put(m, c):
    text = m.group("text").strip().strip("\"«»“”")
    return ("clipboard.set", {"text": text}) if text else None

add("clipboard.clear", r"^(?:очисти|почисти|clear)\s+(?:буфер(?: обмена)?|(?:the )?clipboard)$", "clipboard.set", clear=True)
add("recycle.empty", r"^(?:очисти|почисти|опустоши|empty|clear)\s+(?:корзину|(?:the )?recycle bin|(?:the )?trash|(?:the )?bin)$",
    "recycle.empty")

@rule("type.text", r"^(?:напечатай|введи текст|набери текст|type(?: out)?)\s*[:\-]?\s+(?P<text>.+)$")
def _type(m, c):
    text = m.group("text").strip().strip("\"«»")

    if re.match(r"type\s", c.low) and not re.match(r"type\s*[:\-]|type out\b|type\s+[\"«“']", c.low) and re.match(
            r"(?:of|in|for|is|are|was|vs\.?|versus|hints?|errors?|annotations?|safety|checking|checker|script|system|"
            r"casting|conversion|inference|alias(?:es)?|definitions?|parameters?|guard)\b", text, re.I):
        return None
    return ("type.text", {"text": text}) if text else None

@rule("keys.press", r"^(?:нажми|press)\s+(?P<keys>(?:(?:ctrl|alt|shift|win|контрол|альт|шифт)\s*\+\s*)+\S+)$")
def _keys(m, c):
    keys = re.sub(r"\s*\+\s*", "+", m.group("keys"))
    keys = keys.replace("контрол", "Ctrl").replace("альт", "Alt").replace("шифт", "Shift")
    return "keys.press", {"keys": keys}

_SYS = [
    (r"заряд\w*|батаре\w*|аккумулятор\w*|battery", "battery"),
    (r"процессор\w*|цп|cpu|загрузк\w* (?:процессора|цп)", "cpu"),
    (r"оперативн\w*|оперативк\w*|озу|ram|памят\w*|memory", "ram"),
    (r"(?:свободн\w* )?мест\w* на диске|диск\w*|disk|storage|free space", "disk"),
    (r"(?:мой |my )?(?:ip|айпи)(?: адрес| address)?|сет[ьи]|network", "network"),
    (r"аптайм|uptime|сколько (?:работает|включен) компьютер", "uptime"),
]

@rule("sysinfo", r"^(?:(?:сколько|какой|какая|каков|покажи|show|what(?:'s| is)(?: my| the)?|how much)\s+)?(?P<what>.+?)\??$")
def _sys(m, c):
    what = m.group("what").lower()
    if re.fullmatch(r"(?:информаци\w* о (?:системе|компьютере)|характеристики(?: компьютера| пк)?|system info(?:rmation)?|specs|сведения о системе)", what):
        return "sysinfo.get", {"what": "all"}
    for pattern, key in _SYS:
        if re.fullmatch(rf"(?:(?:у меня|осталось|сейчас|of the|the)\s+)?(?:{pattern})(?:\s+(?:осталось|сейчас|left|usage|level|компьютера|ноутбука))?", what):
            if key == "ram" and not re.search(r"оператив|озу|ram|memory|памят", what):
                return None
            return "sysinfo.get", {"what": key}
    return None

@rule("process.kill", r"^(?:убей|заверши|закрой принудительно|kill|end|terminate)\s+(?:процесс|process|задачу|task)\s+(?P<name>.+)$")
def _kill(m, c):
    name = m.group("name").strip().strip("\"«»")
    if name.isdigit():
        return "process.kill", {"pid": int(name)}
    return "process.kill", {"name": name}

@rule("process.kill_pid", r"^(?:kill|taskkill|убей)\s+(?:-9\s+|-kill\s+|/f\s+)?(?:/pid\s+|-pid\s+)?(?:/f\s+)?(?P<pid>\d{1,7})(?:\s+/f)?$")
def _kill_pid(m, c):
    """Round 34: «kill -9 1234», «taskkill /f /pid 1234», «убей 1234» — a process by its id (was «app.close "-9 1234"»)."""
    return "process.kill", {"pid": int(m.group("pid"))}

add("process.list", r"^(?:список процессов|какие процессы (?:запущены|работают)|что (?:сейчас )?запущено|processes|list processes|running processes)\??$",
    "process.list")

_WIN = r"(?:\s+(?:окно|это окно|окошко|это|его|е[её]|window|this window|the window|this|it))?"
add("window.maximize", rf"^(?:разверни|развернуть|распахни|на весь экран|максимизируй|maximi[sz]e){_WIN}(?:\s+на весь экран)?$",
    "window.action", action="maximize")
add("window.minimize_all", r"^(?:сверн(?:и|уть) все(?: окна)?|покажи рабочий стол|minimi[sz]e all(?: windows)?|show (?:the )?desktop)$",
    "window.minimize_all")
add("window.minimize", rf"^(?:сверни|свернуть|minimi[sz]e|hide){_WIN}$", "window.action", action="minimize")
add("window.restore", rf"^(?:восстанови|верни размер|restore){_WIN}$", "window.action", action="restore")
add("window.snap_left", rf"^(?:(?:прикрепи|подвинь|двинь|перемести|сдвинь|snap|move){_WIN}\s+|(?:окно|window)\s+)?(?:влево|налево|к левому краю|слева|(?:to the )?left)$",
    "window.action", action="snap_left")
add("window.snap_right", rf"^(?:(?:прикрепи|подвинь|двинь|перемести|сдвинь|snap|move){_WIN}\s+|(?:окно|window)\s+)?(?:вправо|направо|к правому краю|справа|(?:to the )?right)$",
    "window.action", action="snap_right")
add("window.center", rf"^(?:отцентрируй|(?:поставь|перемести){_WIN} по центру|center|centre){_WIN}$", "window.action", action="center")
add("window.topmost", rf"^(?:закрепи{_WIN} поверх(?: всех)?(?: окон)?|поверх всех окон|(?:make|keep)(?: it| this window)? (?:on top|always on top)|always on top)$",
    "window.action", action="topmost")

@rule("window.move", r"^(?:перенеси|перемести|перекинь|отправь|move|send)(?:\s+(?:окно|это окно|window|this window|it))?\s+"
      r"(?:на|to)\s+(?:the\s+)?(?P<which>друг\w+|следующ\w+|предыдущ\w+|втор\w+|перв\w+|трет\w+|левый|правый|other|next|previous|prev|second|first|third|left|right|\d)"
      r"\s+(?:монитор|экран|дисплей|monitor|screen|display)$")
def _wmove(m, c):
    which = m.group("which").lower()
    table = {"втор": "2", "second": "2", "перв": "1", "first": "1", "трет": "3", "third": "3",
             "предыдущ": "prev", "previous": "prev", "prev": "prev", "левый": "prev", "left": "prev"}
    for key, value in table.items():
        if which.startswith(key):
            return "window.move", {"monitor": value}
    if which.isdigit():
        return "window.move", {"monitor": which}
    return "window.move", {"monitor": "next"}

@rule("window.arrange", r"^(?:расставь|разложи|упорядочи|выровняй|tile|arrange)\s+(?:все\s+|all\s+)?(?:окна|windows)(?P<how>.*)$")
def _arrange(m, c):
    how = m.group("how").lower()
    layout = "side_by_side"
    if re.search(r"сетк|grid", how):
        layout = "grid"
    elif re.search(r"каскад|cascade", how):
        layout = "cascade"
    elif re.search(r"стопк|друг под другом|stack|vertical", how):
        layout = "stack"
    return "window.arrange", {"layout": layout}

add("desktop.new", r"^(?:(?:создай|новый|открой новый|create a?|new)\s+)(?:виртуальный\s+)?(?:рабочий стол|(?:virtual )?desktop)$",
    "desktop.virtual", action="new")
add("desktop.next", r"^(?:следующий|правый|next)\s+(?:виртуальный\s+)?(?:рабочий стол|(?:virtual )?desktop)$", "desktop.virtual", action="next")
add("desktop.prev", r"^(?:предыдущий|левый|previous|prev)\s+(?:виртуальный\s+)?(?:рабочий стол|(?:virtual )?desktop)$", "desktop.virtual", action="prev")
add("desktop.close", r"^(?:закрой|удали|close)\s+(?:этот\s+|the\s+|this\s+)?(?:виртуальный\s+)?(?:рабочий стол|(?:virtual )?desktop)$",
    "desktop.virtual", action="close")
add("desktop.view", r"^(?:все рабочие столы|представление задач|task view|show (?:all )?desktops)$", "desktop.virtual", action="view")

@rule("layout.capture", r"^(?:сохрани|запомни|save|remember)\s+(?:эту\s+|текущую\s+|this\s+|the\s+|current\s+)?(?:раскладку|расположение|layout|arrangement)"
      r"(?:\s+окон|\s+of windows|\s+windows)?(?:\s+(?:как|под названием|named|as|called))?\s*(?P<name>.*)$")
def _lcap(m, c):
    name = m.group("name").strip(" \"«»:")
    return "layout.capture", ({"name": name[:60]} if name else {})

@rule("layout.restore", r"^(?:восстанови|верни|примени|разложи|restore|apply)\s+(?:раскладку|расположение|layout)(?:\s+окон|\s+of windows)?\s*(?P<name>.*)$")
def _lres(m, c):
    name = m.group("name").strip(" \"«»:")
    return "layout.restore", {"name": name[:60] or "default"}

_ENGINES = {"гугл": "google", "google": "google", "яндекс": "yandex", "yandex": "yandex", "бинг": "bing", "bing": "bing",
            "duckduckgo": "duckduckgo", "дакдакго": "duckduckgo"}

@rule("web.search", r"^(?:загугли|погугли|google|поищи|найди|search(?: for)?|look up|search the web for)"
      r"(?P<where>\s+(?:в интернете|в сети|в гугле|в google|в яндексе|в yandex|в бинге|on the web|online|on google|in google))?\s+(?P<q>.+)$")
def _search(m, c):
    verb = c.low.split(" ", 1)[0]
    where = (m.group("where") or "").lower()
    if verb in ("найди", "поищи", "search") and not where:
        return None
    engine = None
    for key, value in _ENGINES.items():
        if key in where or (verb in ("загугли", "погугли", "google") and value == "google"):
            engine = value
            break
    q = m.group("q").strip()
    return "web.search", {"query": q, **({"engine": engine} if engine else {})}

_MUSIC = {"ютуб": "youtube", "youtube": "youtube", "ютюб": "youtube", "youtube music": "youtube_music",
          "ютуб музык": "youtube_music", "яндекс музык": "yandex_music", "yandex music": "yandex_music",
          "spotify": "spotify", "спотифа": "spotify", "вк": "vk", "vk": "vk", "soundcloud": "soundcloud", "саундклауд": "soundcloud"}

@rule("media.search", r"^(?:включи|поставь|найди|открой|play|put on|find)\s+(?P<q>.+?)\s+(?:на|в|on|in)\s+(?P<svc>ютуб\w*|youtube(?: music)?|ютюб\w*|"
      r"яндекс музык\w*|yandex music|spotify|спотифа\w*|вк|vk|soundcloud|саундклауд\w*)$")
def _media(m, c):
    svc = m.group("svc").lower()
    service = next((v for k, v in _MUSIC.items() if svc.startswith(k)), "youtube")
    return "media.search", {"query": m.group("q").strip(" \"«»"), "service": service}

@rule("media.search2", r"^(?:включи|поставь|найди|play|put on)\s+(?:песню|музыку|трек|клип|видео|song|music|track|the song|video)\s+(?P<q>.+)$")
def _media2(m, c):
    return "media.search", {"query": m.group("q").strip(" \"«»")}

@rule("youtube.search", r"^(?:ютуб|youtube)\s+(?P<q>.+)$")
def _yt(m, c):
    return "media.search", {"query": m.group("q").strip(), "service": "youtube"}

_FOOD_SVC = {"яндекс ед": "yandex_eda", "yandex eda": "yandex_eda", "деливери": "delivery_club", "delivery club": "delivery_club",
             "самокат": "samokat", "купер": "kuper", "сбермаркет": "kuper", "uber ?eats": "ubereats", "doordash": "doordash",
             "deliveroo": "deliveroo", "wolt": "wolt", "волт": "wolt"}
_SHOP_SVC = {"озон": "ozon", "ozon": "ozon", "вайлдберри": "wildberries", "wildberries": "wildberries", "вб": "wildberries",
             "яндекс маркет": "yandex_market", "маркет": "yandex_market", "amazon": "amazon", "амазон": "amazon",
             "алиэкспресс": "aliexpress", "aliexpress": "aliexpress", "али": "aliexpress", "авито": "avito", "avito": "avito",
             "ebay": "ebay"}

@rule("order", r"^(?:закажи|заказать|хочу заказать|order|get me|buy me|купи)\s+(?:мне\s+|me\s+|some\s+)?(?P<q>.+)$")
def _order(m, c):
    q = m.group("q").strip()
    low = q.lower()
    if re.match(r"^(?:такси|taxi|a taxi|a cab|cab|uber)\b", low):
        dest = re.search(r"\b(?:до|в|на|to)\s+(.+)$", q, re.I)
        return "taxi.order", ({"to": dest.group(1).strip()} if dest else {})
    service = None
    svc_match = re.search(r"\s+(?:в|на|через|из|on|from|via|at)\s+(?P<svc>[\w\s]+)$", q, re.I)
    if svc_match:
        name = svc_match.group("svc").lower()
        for key, value in {**_FOOD_SVC, **_SHOP_SVC}.items():
            if re.match(key, name):
                service = value
                q = q[:svc_match.start()].strip()
                break
    low = q.lower()
    is_food = (service in _FOOD_SVC.values()) or any(word in low for word in FOOD_WORDS)
    if service in _SHOP_SVC.values():
        is_food = False
    action = "food.order" if is_food else "shop.search"
    return action, {"query": q, **({"service": service} if service else {})}

@rule("taxi", r"^(?:вызови|закажи|call|get|book)\s+(?:мне\s+|me\s+)?(?:такси|taxi|a taxi|a cab|cab|uber)(?:\s+(?:до|в|на|to)\s+(?P<to>.+))?$")
def _taxi(m, c):
    return "taxi.order", ({"to": m.group("to").strip()} if m.group("to") else {})

@rule("maps.route", r"^(?:маршрут|(?:проложи|построй) маршрут|как доехать|как добраться|route|directions|navigate)\s+(?:до|в|к|на|to)\s+(?P<to>.+?)(?:\s+(?:от|из|from)\s+(?P<from>.+))?$")
def _route(m, c):
    params = {"to": m.group("to").strip()}
    if m.group("from"):
        params["from"] = m.group("from").strip()
    return "maps.route", params

@rule("maps.find", r"^(?:найди на карте|покажи на карте|где находится|show on (?:the )?map|find on (?:the )?map)\s+(?P<to>.+)$")
def _mapfind(m, c):
    return "maps.route", {"to": m.group("to").strip()}

@rule("weather", r"^(?:какая\s+)?(?:погода|погоду|прогноз(?: погоды)?|weather|forecast|what(?:'s| is) the weather(?: like)?)"
      r"(?P<rest>.*)$")
def _weather(m, c):
    rest = m.group("rest").strip(" ?")

    if re.match(r"(?:какая\s+)?(?:прогноз|forecast)\b", c.low) and not re.match(
            r"(?:прогноз\s+погоды|forecast\s+weather)", c.low) and rest and not re.match(
            r"(?:на|в|во|для|по|for|in|at|сейчас|сегодня|завтра|now|today|tomorrow|this|next)\b", rest, re.I):
        return None

    if re.search(r"\b(?:обычно|в среднем|средн\w*|климат\w*|бывает|usually|typically|average|normally|climate|"
                 r"январ\w*|феврал\w*|март\w*|апрел\w*|ма[ея]|июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|"
                 r"декабр\w*|летом|зимой|весной|осенью|january|february|march|april|may|june|july|august|september|"
                 r"october|november|december|summer|winter|spring|autumn|fall|в прошлом|last year|history|historical)\b",
                 rest, re.I):
        return None
    when = "now"
    if re.search(r"\b(?:завтра|tomorrow)\b", rest, re.I):
        when = "tomorrow"
    elif re.search(r"\b(?:на неделю|неделя|week)\b", rest, re.I):
        when = "week"
    elif re.search(r"\b(?:сегодня|today)\b", rest, re.I):
        when = "today"
    city = re.sub(r"\b(?:сейчас|сегодня|завтра|на неделю|на завтра|будет|now|today|tomorrow|this week|for the week|like|"
                  r"will be)\b", " ", rest, flags=re.I)
    city = re.sub(r"\s+", " ", city).strip()
    for _ in range(2):
        city = re.sub(r"^\s*(?:в|во|на|in|for|at)(?:\s+|$)", "", city.strip(), flags=re.I).strip(" ,?")
    if len(city) > 40:
        return None
    return "weather.get", {"when": when, **({"city": city} if city else {})}

_CUR = {"доллар": "USD", "бакс": "USD", "usd": "USD", "dollar": "USD", "\\$": "USD", "евро": "EUR", "eur": "EUR", "euro": "EUR",
        "€": "EUR", "рубл": "RUB", "rub": "RUB", "ruble": "RUB", "₽": "RUB", "фунт": "GBP", "gbp": "GBP", "pound": "GBP",
        "юан": "CNY", "cny": "CNY", "yuan": "CNY", "иен": "JPY", "йен": "JPY", "jpy": "JPY", "yen": "JPY", "тенге": "KZT",
        "kzt": "KZT", "лир": "TRY", "try": "TRY", "гривн": "UAH", "uah": "UAH", "франк": "CHF", "chf": "CHF",
        "биткоин": "BTC", "btc": "BTC", "bitcoin": "BTC", "эфир": "ETH", "eth": "ETH", "ethereum": "ETH", "дирхам": "AED", "aed": "AED",
        "злот": "PLN", "pln": "PLN", "белорусск": "BYN", "byn": "BYN", "драм": "AMD", "amd": "AMD", "лари": "GEL", "gel": "GEL"}
_CUR_RE = "|".join(sorted(_CUR, key=len, reverse=True))

def _currency_code(word: str) -> str:
    low = word.lower()
    for key, code in _CUR.items():
        if re.match(key, low):
            return code
    return ""

@rule("currency.rate", rf"^(?:какой\s+)?(?:курс|exchange rate(?: of)?|rate(?: of)?)\s+(?P<a>(?:{_CUR_RE})\w*)(?:\s+(?:к|в|to|in)\s+(?P<b>(?:{_CUR_RE})\w*))?\??$")
def _rate(m, c):
    a = _currency_code(m.group("a"))
    b = _currency_code(m.group("b") or "") or ("USD" if a == "RUB" else "RUB" if re.search(r"[а-я]", c.low) else "USD")
    return ("currency.convert", {"amount": 1, "from": a, "to": b}) if a and a != b else None

@rule("currency.convert", rf"^(?:сколько будет\s+|convert\s+|переведи\s+)?(?P<n>\d+(?:[.,]\d+)?)\s*(?P<a>(?:{_CUR_RE})\w*)\s+(?:в|to|in)\s+(?P<b>(?:{_CUR_RE})\w*)\??$")
def _convert(m, c):
    a, b = _currency_code(m.group("a")), _currency_code(m.group("b"))
    value = parse_number(m.group("n"))
    return ("currency.convert", {"amount": value, "from": a, "to": b}) if a and b and a != b and value is not None else None

add("time.now", r"^(?:который час|сколько (?:сейчас )?времени|какое сейчас время|what time is it|what's the time|current time|"
    r"какое (?:сегодня )?число|какая (?:сегодня )?дата|what(?:'s| is) (?:the |today's )?date)\??$", "time.now")

@rule("image.generate", r"^(?:нарисуй|сгенерируй|создай|сделай|draw|generate|create|make|imagine|paint)\s+"
      r"(?:мне\s+|me\s+)?(?:(?:картинку|изображение|рисунок|арт|фото|an? image|an? picture|a drawing|art|image|picture)\s*)?(?:(?:с|со|of|with|:|—|-)\s+)?(?P<p>.+)$")
def _image(m, c):
    verb = c.low.split(" ", 1)[0]
    if verb in ("создай", "сделай", "create", "make", "generate", "сгенерируй") and not re.search(
            r"\b(?:картинк|изображени|рисун|арт|фото|image|picture|drawing|art)\w*", c.low):
        return None
    prompt = m.group("p").strip()
    if re.fullmatch(r"(?:картинк\w*|изображени\w*|рисун\w*|арт|фот\w*|снимок|images?|pictures?|photos?|drawing|art)",
                    prompt, re.I):
        return None

    from .media_routing import heuristic
    if heuristic(c.low)[0] == "text":
        return None
    return ("image.generate", {"prompt": prompt}) if len(prompt) >= 2 else None

@rule("note.add", r"^(?:(?:запиши|сохрани|добавь)\s+(?:в\s+)?заметк\w*|заметка|note|take a note|save a note|add a note|запиши)\s*[:\-—,]?\s+(?P<t>.+)$")
def _note(m, c):
    text = m.group("t").strip()
    if re.match(r"запиши\s", c.low) and not re.match(r"запиши\s+(?:в\s+)?заметк", c.low) and re.match(
            r"(?:видео|экран\w*|звук\w*|голос\w*|аудио|разговор\w*|звонок|созвон\w*|встреч\w*|скринкаст\w*|"
            r"video|screen|audio|sound|voice|call|meeting)\b", text, re.I):
        return None
    return "note.add", {"text": text}

@rule("memuai.chat", r"^(?:отправь|передай|send)\s+(?:в\s+)?(?:чат(?: memuai| мему)?|memuai|мему|to (?:the )?(?:memuai )?chat)\s*[:\-—]?\s+(?P<t>.+)$")
def _tochat(m, c):
    return "memuai.chat", {"text": m.group("t").strip()}

@rule("memuai.open", r"^(?:открой|покажи|open|show)\s+(?:(?:настройки|settings)\s+)?(?:memuai|мему|мемуаи)(?P<rest>.*)$|"
      r"^(?:открой|покажи|open|show)\s+(?P<rest2>.+?)\s+(?:в\s+memuai|в\s+мему|memuai|in memuai)$")
def _mopen(m, c):
    rest = (m.group("rest") or m.group("rest2") or "").strip().lower()
    if not rest:
        if re.search(r"настройки|settings", c.low):
            return "memuai.open", {"target": "settings"}
        return "memuai.open", {"target": "main"}
    for pattern, key in MEMUAI_SECTIONS:
        if re.search(pattern, rest):
            return "memuai.open", {"target": key}
    if re.search(r"настройки|settings", rest):
        return "memuai.open", {"target": "settings"}
    return None

@rule("memuai.settings", r"^(?:открой\s+|open\s+)?(?:настройки|settings)\s+(?:быстрого доступа|quick ask|голоса|voice|панелей|panels|моделей|models)$")
def _msettings(m, c):
    for pattern, key in MEMUAI_SECTIONS:
        if re.search(pattern, c.low.split(" ", 1)[-1]):
            return "memuai.open", {"target": key}
    return None

@rule("open.settings", r"^(?:открой\s+|open\s+|покажи\s+)?(?:параметры|настройки|settings)(?:\s+windows)?(?P<rest>(?:\s+.+)?)$")
def _settings(m, c):
    rest = m.group("rest").strip().lower()
    if not rest:
        return "open.settings", {}
    if re.search(r"memuai|мему|быстр", rest):
        return None
    for pattern, key in SETTINGS_WORDS:
        if re.search(rf"\b(?:{pattern})", rest):
            return "open.settings", {"page": key}
    return None

@rule("open.folder", r"^(?:открой|покажи|open|show)\s+(?:папку\s+|the\s+|my\s+|folder\s+)?(?P<f>[\w\s-]+?)(?:\s+(?:folder|папку))?$")
def _folder(m, c):
    word = m.group("f").strip().lower()
    for pattern, key in FOLDER_WORDS:
        if re.fullmatch(rf"(?:{pattern})", word):
            return "open.folder", {"folder": key}
    return None

@rule("open.url", r"^(?:открой|зайди на|перейди на|open|go to|visit)\s+(?:сайт\s+|the site\s+|website\s+)?(?P<u>(?:https?://)?[\w.-]+\.[a-zа-я]{2,}(?:/\S*)?)$")
def _url(m, c):
    return "open.url", {"url": m.group("u")}

@rule("open.site", r"^(?:открой|зайди на|зайди в|перейди на|open|go to)\s+(?:сайт\s+)?(?P<s>.+)$")
def _site(m, c):
    name = m.group("s").strip().lower()
    if name in KNOWN_SITES:
        return "open.url", {"url": KNOWN_SITES[name]}
    return None

add("assistant.help", r"^(?:что ты умеешь|что ты можешь|помощь|какие (?:есть )?команды|список команд|помощь по командам|команды|help|commands|what can you do)\??$",
    "assistant.help")
add("assistant.undo", r"^(?:отмени(?: это| последнее(?: действие)?)?|верни как было|undo(?: that| last)?)$", "assistant.undo")

add("app.list", r"^(?:какие (?:окна|программы|приложения) (?:открыты|запущены|сейчас открыты)|список (?:окон|открытых окон|"
    r"открытых программ)|открытые окна|что открыто|list (?:the )?(?:open )?windows|(?:what|which) (?:windows|apps) are open|open windows)$",
    "app.list")

@rule("window.app", r"^(?P<verb>сверни|свернуть|разверни|развернуть|восстанови|minimi[sz]e|maximi[sz]e|restore)\s+"
      r"(?:окно\s+|window\s+|the\s+)?(?P<n>[^\s].{0,40})$")
def _window_app(m, c):
    name = m.group("n").strip(" \"«»")
    if re.fullmatch(r"(?:окно|это окно|окошко|это|его|е[её]|все|все окна|window|this window|the window|this|it|all|everything|"
                    r"на весь экран)", name, re.I) \
            or _compound(name):
        return None
    verb = m.group("verb").lower()
    action = "minimize" if verb.startswith(("свер", "minim")) else "maximize" if verb.startswith(("разв", "maxim")) else "restore"
    return "window.action", {"action": action, "target": name}

@rule("app.close", r"^(?:закрой|закрыть|закрывай|выключи|выйди из|заверши|завершить|завершай|выруби|вырубай|вырубить|убей|"
                   r"close|quit|exit|kill)\s+(?P<n>.+)$")
def _close(m, c):
    name = m.group("n").strip(" \"«»")
    if _compound(name):
        return None
    if re.fullmatch(r"(?:окно|это окно|это|его|е[её]|window|this window|this|it)", name, re.I):
        return "window.action", {"action": "close"}
    if re.search(r"\b(?:все|all)\b", name, re.I) or len(name) > 60:
        return None
    return "app.close", {"name": name}

def _known_app(name: str) -> bool:
    """Round 34: is ``name`` an app we know by name (the alias table or the already built app index)?"""
    from . import assistant_apps as apps
    found = apps._candidates(re.sub(r"^(?:мне|нам|me|us)\s+", "", name.strip(), flags=re.I))
    keys = {fold(x) for x in found}
    if any(canonical in found for canonical in apps.ALIASES):
        return True
    for entry in list(apps._CACHE.get("items") or ()) or apps.builtin_entries():
        if entry.get("key") in keys or any(fold(a) in keys for a in entry.get("aliases") or ()):
            return True
    return False

@rule("app.switch", r"^(?P<verb>переключись на|переключи на|переключиться на|перейди в|перейди к|покажи окно|покажи|верни окно|"
                    r"switch to|go to|focus|bring up|show)\s+(?P<n>.+)$")
def _switch(m, c):
    name = m.group("n").strip(" \"«»")
    if _compound(name) or len(name) > 60:
        return None

    verb = m.group("verb").lower()
    if verb in ("покажи", "show", "перейди в", "перейди к", "go to", "focus") \
            and (re.match(r"(?:мне|me|the|my|a|an|on)\s", name, re.I)
                 or not (_known_app(name) or (verb[0] == "п" and re.fullmatch(r"[a-z][\w.+-]{1,24}", name, re.I)))):
        return None
    return "app.switch", {"name": name}

@rule("app.open", r"^(?P<verb>открой|запусти|включи|открыть|запустить|стартуй|launch|open|start|run)\s+(?:приложение\s+|программу\s+|app\s+|the app\s+)?(?P<n>.+)$")
def _open(m, c):
    name = m.group("n").strip(" \"«»")
    settings = re.fullmatch(r"(?:windows\s+)?(.+?)\s+settings", name, re.I)
    if settings:
        key = next((k for pattern, k in SETTINGS_WORDS if re.search(rf"\b(?:{pattern})", settings.group(1).lower())), None)
        if key:
            return "open.settings", {"page": key}
    if _compound(name):
        return None
    if len(name) > 60 or not name:
        return None

    if (len(name.split()) >= 3 and re.search(r"\s(?:на|про|о|об|обо|по|для|между|и|или|с|со|без|от|из|к|ко|у|над|под|"
                                              r"чтобы|on|about|for|between|and|or|of|with|to|from|into|in)\s", name, re.I)
            or re.match(r"(?:мне|нам|me|us)\s", name, re.I)) and not _known_app(name):
        return None
    return "app.open", {"name": name, "_soft": m.group("verb").lower() in ("включи", "start", "run")}

_COMPOUND = re.compile(
    r"(?:\s+(?:и|а потом|потом|затем|и потом|and|then|and then)|,)\s+(?:найди|открой|включи|запусти|закрой|поставь|"
    r"сделай|напиши|набери|перейди|зайди|покажи|выключи|скачай|отправь|find|open|play|search|start|launch|close|set|"
    r"make|type|write|go|show|turn|download|send)\b", re.I)

def _compound(name: str) -> bool:
    """«хром и найди котиков» — two requests in one: the model does both, the local matcher only one."""
    return bool(_COMPOUND.search(" " + name))

_QUESTION = re.compile(r"^(?:как|почему|зачем|что такое|что значит|объясни|расскажи|можно ли|how|why|what is|what are|"
                       r"explain|tell me about|is it|should i|where is)\b", re.I)
_QUESTION_OK = {"volume.get", "brightness.get", "sysinfo.get", "time.now", "weather.get", "currency.convert",
                "clipboard.get", "timer.list", "assistant.help", "reminder.list", "pomodoro.control", "process.list",
                "maps.route", "unit.convert",
                "text.translate"}

def match_custom(text: str, commands: Iterable[Dict[str, Any]], threshold: float = 0.86) -> Optional[Dict[str, Any]]:
    """The user's own command whose trigger phrase matches ``text`` (exact first, then fuzzy)."""
    norm = normalize(text).lower()
    if not norm:
        return None
    key = fold(norm)
    best: Tuple[float, Optional[Dict[str, Any]], str] = (0.0, None, "")
    for command in commands or []:
        if not isinstance(command, dict) or command.get("enabled") is False:
            continue
        for trigger in command.get("triggers") or []:
            trig = normalize(trigger).lower()
            if not trig:
                continue
            if fold(trig) == key:
                return {"command": command, "score": 1.0, "trigger": trigger}
            score = similarity(trig, norm)
            if score > best[0]:
                best = (score, command, trigger)
    if best[1] is not None and best[0] >= threshold:
        return {"command": best[1], "score": round(best[0], 3), "trigger": best[2]}
    return None

CHAT_AMBIGUOUS = {"media.pause", "media.play", "media.next", "media.prev", "window.maximize", "window.minimize",
                  "window.restore", "window.snap_left", "window.snap_right", "window.center", "stopwatch.lap",
                  "assistant.undo", "assistant.help", "sysinfo", "lock.screen", "power.restart", "power.sleep",
                  "volume.get", "brightness.get", "timer.list", "reminder.list", "weather"}

CHAT_CONTEXT = {"r24.summarize", "r24.translate", "r24.translate_tail"}

def layout_phrase(text: str) -> str:
    """Round 34: a whole phrase typed in the wrong keyboard layout — «jnrhjq [hjv» → «открой хром», «uhjvrjcnm 50» →
    «громкость 50», «щзут сркщьу» → «open chrome» ("" when the text is not one of those)."""
    from .assistant_apps import _EN2RU, _RU2EN
    raw = str(text or "").strip().lower()
    if not raw or len(raw) > 120:
        return ""
    if re.fullmatch(r"[a-z\[\];',.`\s\d%+-]+", raw) and re.search(r"[a-z]", raw):
        out = raw.replace("`", "ё").translate(_EN2RU)
        return out if re.search(r"[а-яё]{3,}", out) else ""
    if re.fullmatch(r"[а-яё\s\d%+-]+", raw):
        out = raw.replace("ё", "`").translate(_RU2EN)
        return out if re.fullmatch(r"[a-z\s\d%+-]+", out) and re.search(r"[a-z]{3,}", out) else ""
    return ""

def match(text: Any, *, now: Optional[datetime] = None, commands: Iterable[Dict[str, Any]] = (),
          disabled: Iterable[str] = (), conversation: bool = False, _swapped: bool = False) -> Optional[Dict[str, Any]]:
    """Local intent for ``text``: ``{"action", "params", "confidence", "rule"}`` or a custom command hit
    ``{"action": "command.run", "params": {"name", "id"}, ...}``; ``None`` → let the model decide.

    ``conversation`` — the phrase is typed into an ongoing chat (Round 23): one-word replies that are
    also commands (:data:`CHAT_AMBIGUOUS`) are left to the model."""
    raw = str(text or "")
    if not raw.strip() or "\n" in raw.strip() or len(raw) > 400:
        return None
    clean = normalize(raw)
    if not clean:
        return None
    custom = match_custom(clean, commands)
    if custom:
        command = custom["command"]
        return {"action": "command.run", "params": {"name": str(command.get("name") or ""), "id": str(command.get("id") or "")},
                "confidence": custom["score"], "rule": "custom", "trigger": custom["trigger"]}
    question = clean.endswith("?") or bool(_QUESTION.match(clean))
    ctx = Ctx(clean.rstrip("?").strip() if not clean.endswith("?") else clean, now or datetime.now())
    blocked = set(disabled or ())
    one_word = len(clean.rstrip("?!. ").split()) <= 1
    for name, pattern, fn in _RULES:
        found = pattern.search(ctx.text.rstrip("?").strip()) if name not in ("sysinfo",) else pattern.search(ctx.text)
        if not found:
            continue
        if conversation and one_word and name in CHAT_AMBIGUOUS:
            return None
        try:
            result = fn(found, ctx)
        except Exception:
            result = None
        if not result:
            continue
        action, params = result
        if conversation and name in CHAT_CONTEXT and not params.get("text") and not params.get("page"):
            return None
        if question and action not in _QUESTION_OK:
            return None
        if action in blocked:
            return None
        soft = bool(params.pop("_soft", False))
        return {"action": action, "params": params, "confidence": 0.75 if soft else 0.95, "rule": name,
                "fallback_on_miss": action in ("app.open", "app.close", "app.switch", "schedule.plan")
                or (action == "window.action" and bool(params.get("target")))}
    if not _swapped and not conversation:
        alt = layout_phrase(clean)
        if alt:
            found = match(alt, now=now, commands=commands, disabled=disabled, _swapped=True)
            if found:
                found["layout"] = alt
                return found
    return None

def rules() -> List[str]:
    return [name for name, _p, _f in _RULES]
