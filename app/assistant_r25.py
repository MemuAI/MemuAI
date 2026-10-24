"""Round 25 (a6-quick): инструменты быстрого доступа — без сети, без модели, мгновенно.

Подключение такое же, как у ``assistant_r24``: ``install()`` при импорте ``assistant_commands`` (действия → в
``assistant_catalog.ACTIONS/BY_ID``, фразы → В НАЧАЛО ``assistant_intents._RULES``, справочные действия → в
``_QUESTION_OK``), ``bind(engine)`` в ``Engine.__init__`` (обработчики → ``engine.handlers``).

Действия (категория «Инструменты», все кроссплатформенные, уровень safe):
  color.convert   — цвет: HEX ↔ RGB ↔ HSL, образец цвета на карточке
  text.encode     — Base64 / URL: закодировать или раскодировать (текст или буфер)
  json.format     — JSON: красиво отформатировать, сжать или только проверить (с позицией ошибки)
  hash.text       — MD5 / SHA-1 / SHA-256 / SHA-384 / SHA-512 текста или буфера
  text.case       — регистр и стиль: ВЕРХНИЙ, нижний, Заголовок, snake_case, camelCase, kebab-case, транслит
  text.stats      — слова, символы, строки, предложения, время чтения
  date.calc       — дней до даты / между датами, дата через N дней (недель, месяцев), день недели
  time.zone       — время в другом городе и перевод времени между городами (своя таблица поясов, без tzdata)
  random.pick     — случайное число, монетка, кубики (2d6), выбор из списка
  lorem.generate  — «рыба» Lorem ipsum (слова или абзацы)

Результат трансформаций (encode, json, hash, case, lorem) сразу копируется в буфер, как у перевода из Round 24.
Текст пользователя не пишется в журнал действий (``NO_AUDIT_PARAMS``).
QR-код и проверка регулярных выражений сознательно не добавлены: в сборке нет библиотеки QR, а пользовательский
regex на Python нельзя прервать по тайм-ауту (катастрофический возврат повесил бы поток команд).
"""
from __future__ import annotations

import base64
import binascii
import calendar
import colorsys
import hashlib
import json
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote

from . import assistant_catalog as catalog

A, _s, _n, _e = catalog.A, catalog._s, catalog._n, catalog._e

CASES = ("upper", "lower", "title", "sentence", "snake", "camel", "kebab", "translit")
HASHES = ("md5", "sha1", "sha256", "sha384", "sha512")

NEW_ACTIONS: List[Dict[str, Any]] = [
    A("color.convert", "tools", "safe", "Цвет: HEX, RGB, HSL", "Color: HEX, RGB, HSL",
      "HEX/RGB/HSL color. Accepts \"#ff8800\", \"rgb(255,136,0)\", \"hsl(32,100%,50%)\".",
      {"color": _s("The color in any of the three notations")}, ("color",), windows=False),
    A("text.encode", "tools", "safe", "Base64 и URL-кодирование", "Base64 and URL encoding",
      "Base64/URL encode or decode. Empty text = the clipboard; the result is copied.",
      {"text": _s("Text; empty = clipboard"), "codec": _e(("base64", "url")),
       "op": _e(("encode", "decode", "auto"), "auto = decode when the text is valid Base64, else encode")},
      ("codec",), windows=False),
    A("json.format", "tools", "safe", "Форматировать JSON", "Format JSON",
      "Format/minify/validate JSON. Empty text = the clipboard; errors name the line and column.",
      {"text": _s("JSON; empty = clipboard"), "mode": _e(("pretty", "minify", "validate"))}, windows=False),
    A("hash.text", "tools", "safe", "Хеш текста", "Hash text",
      "Hash of text. UTF-8; empty text = the clipboard; the hex digest is copied.",
      {"text": _s("Text; empty = clipboard"), "algo": _e(HASHES)}, windows=False),
    A("text.case", "tools", "safe", "Регистр и стиль текста", "Text case",
      "Change text case or transliterate. Empty text = the clipboard; the result is copied.",
      {"text": _s("Text; empty = clipboard"), "case": _e(CASES)}, ("case",), windows=False),
    A("text.stats", "tools", "safe", "Статистика текста", "Text statistics",
      "Word/character count. Empty text = the clipboard; also lines, sentences, reading time.",
      {"text": _s("Text; empty = clipboard")}, windows=False),
    A("date.calc", "tools", "safe", "Калькулятор дат", "Date calculator",
      "Days until/between dates, date ± N units, weekday. Dates: YYYY-MM-DD, DD.MM.YYYY, \"9 мая 2027\", "
      "\"May 9\", today, tomorrow; amount < 0 = back in time.",
      {"op": _e(("until", "between", "add", "weekday")), "date": _s("Date (default today)"),
       "date2": _s("Second date (op=between)"), "amount": _n("How many units to add (op=add); negative = back"),
       "unit": _e(("days", "weeks", "months", "years"))}, ("op",), windows=False),
    A("time.zone", "tools", "safe", "Время в другом городе", "Time in another city",
      "Time in a city, or convert HH:MM between cities. Offline table of ~70 cities with their daylight-saving "
      "rules.",
      {"city": _s("Target city"), "time": _s("Time of day HH:MM to convert (empty = now)"),
       "from": _s("City the time is given in (empty = this computer's time zone)")}, ("city",), windows=False),
    A("random.pick", "tools", "safe", "Случайное число, монетка, кубик", "Random number, coin, dice",
      "Random number, coin, dice (NdM) or pick from a list. Cryptographic RNG.",
      {"kind": _e(("number", "coin", "dice", "choice")), "min": _n("Lower bound (number)", integer=True),
       "max": _n("Upper bound (number)", integer=True), "count": _n("Dice count 1–20", 1, 20, integer=True),
       "sides": _n("Dice sides 2–1000", 2, 1000, integer=True), "items": _s("Comma-separated options (choice)")},
      ("kind",), windows=False),
    A("lorem.generate", "tools", "safe", "Текст-рыба Lorem ipsum", "Lorem ipsum",
      "Lorem ipsum placeholder text. Copied to the clipboard.",
      {"words": _n("Words 1–2000", 1, 2000, integer=True), "paragraphs": _n("Paragraphs 1–20", 1, 20, integer=True)},
      windows=False),
]
NEW_IDS = [a["id"] for a in NEW_ACTIONS]
#: Read-only answers: allowed for phrases that look like questions («сколько дней до …?»).
QUESTION_OK = {"color.convert", "text.stats", "date.calc", "time.zone", "hash.text"}
#: The user's text is never written to the action journal.
NO_AUDIT_PARAMS = {"text.encode", "json.format", "hash.text", "text.case", "text.stats"}


# ================================================================== colour
def parse_color(value: str) -> Optional[Tuple[int, int, int, Optional[float]]]:
    """``#rgb``, ``#rgba``, ``#rrggbb``, ``#rrggbbaa``, ``rgb()/rgba()``, ``hsl()/hsla()`` → (r, g, b, alpha|None)."""
    text = str(value or "").strip().lower().replace(" ", "")
    m = re.fullmatch(r"#?([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})", text)
    if m and (text.startswith("#") or re.search(r"[a-f]", m.group(1)) or len(m.group(1)) in (6, 8)):
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return r, g, b, (round(int(h[6:8], 16) / 255, 3) if len(h) == 8 else None)
    m = re.fullmatch(r"rgba?\(([\d.]+%?),([\d.]+%?),([\d.]+%?)(?:[,/]([\d.]+%?))?\)", text)
    if m:
        def channel(v: str) -> int:
            n = float(v[:-1]) * 2.55 if v.endswith("%") else float(v)
            return int(round(min(255.0, max(0.0, n))))
        return channel(m.group(1)), channel(m.group(2)), channel(m.group(3)), _alpha(m.group(4))
    m = re.fullmatch(r"hsla?\(([\d.]+)(?:deg)?,([\d.]+)%?,([\d.]+)%?(?:[,/]([\d.]+%?))?\)", text)
    if m:
        h, s, l = float(m.group(1)) % 360, min(100.0, float(m.group(2))), min(100.0, float(m.group(3)))
        r, g, b = colorsys.hls_to_rgb(h / 360, l / 100, s / 100)
        return int(round(r * 255)), int(round(g * 255)), int(round(b * 255)), _alpha(m.group(4))
    return None


def _alpha(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    a = float(value[:-1]) / 100 if value.endswith("%") else float(value)
    return round(min(1.0, max(0.0, a)), 3)


def color_forms(r: int, g: int, b: int, alpha: Optional[float] = None) -> Dict[str, str]:
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    hsl = (round(h * 360) % 360, round(s * 100), round(l * 100))
    hex_ = "#%02x%02x%02x" % (r, g, b)
    if alpha is None:
        return {"hex": hex_, "rgb": f"rgb({r}, {g}, {b})", "hsl": f"hsl({hsl[0]}, {hsl[1]}%, {hsl[2]}%)"}
    a = ("%g" % alpha)
    return {"hex": hex_ + "%02x" % int(round(alpha * 255)), "rgb": f"rgba({r}, {g}, {b}, {a})",
            "hsl": f"hsla({hsl[0]}, {hsl[1]}%, {hsl[2]}%, {a})"}


# ================================================================== text tools
def b64_decode(text: str) -> str:
    raw = re.sub(r"\s+", "", text)
    raw = raw.replace("-", "+").replace("_", "/")      # URL-safe alphabet too
    raw += "=" * (-len(raw) % 4)
    data = base64.b64decode(raw, validate=True)
    return data.decode("utf-8")


def looks_base64(text: str) -> bool:
    raw = re.sub(r"\s+", "", text)
    if len(raw) < 4 or not re.fullmatch(r"[A-Za-z0-9+/_-]+={0,2}", raw) or not re.search(r"[0-9+/=A-Z]", raw):
        return False
    try:
        out = b64_decode(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return False
    return bool(out) and all(ch.isprintable() or ch in "\r\n\t" for ch in out)


def encode_text(text: str, codec: str, op: str = "auto") -> Tuple[str, str]:
    """→ (result, op actually done). Raises ``ValueError("not_base64" | "not_utf8")`` on bad input."""
    if codec == "url":
        if op == "auto":
            op = "decode" if re.search(r"%[0-9a-fA-F]{2}", text) else "encode"
        return (unquote(text) if op == "decode" else quote(text, safe="")), op
    if op == "auto":
        op = "decode" if looks_base64(text) else "encode"
    if op == "decode":
        try:
            return b64_decode(text), op
        except UnicodeDecodeError:
            raise ValueError("not_utf8") from None
        except (binascii.Error, ValueError):
            raise ValueError("not_base64") from None
    return base64.b64encode(text.encode("utf-8")).decode("ascii"), op


def format_json(text: str, mode: str = "pretty") -> str:
    """Pretty-print or minify; a syntax error propagates as ``json.JSONDecodeError`` (line, column, message)."""
    data = json.loads(text)
    if mode == "minify":
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(data, ensure_ascii=False, indent=2)


def hash_text(text: str, algo: str = "sha256") -> str:
    return hashlib.new(algo, text.encode("utf-8")).hexdigest()


_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "zh", "з": "z", "и": "i", "й": "y",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def translit(text: str) -> str:
    out = []
    for ch in text:
        low = ch.lower()
        if low in _TRANSLIT:
            t = _TRANSLIT[low]
            out.append(t.capitalize() if ch != low and t else t)
        else:
            out.append(ch)
    return "".join(out)


def _words(text: str) -> List[str]:
    spaced = re.sub(r"([a-zа-яё0-9])([A-ZА-ЯЁ])", r"\1 \2", text)      # camelCase → camel Case
    return [w for w in re.split(r"[^0-9A-Za-zА-Яа-яЁё]+", spaced) if w]


def change_case(text: str, case: str) -> str:
    if case == "upper":
        return text.upper()
    if case == "lower":
        return text.lower()
    if case == "title":
        return re.sub(r"[^\W_]+", lambda m: m.group(0)[:1].upper() + m.group(0)[1:].lower(), text)
    if case == "sentence":
        low = text.lower()
        return re.sub(r"(^\s*|[.!?…]\s+)([^\W_])", lambda m: m.group(1) + m.group(2).upper(), low)
    if case == "translit":
        return translit(text)
    words = [w.lower() for w in _words(text)]
    if case == "snake":
        return "_".join(words)
    if case == "kebab":
        return "-".join(words)
    if case == "camel":
        return "".join(w if i == 0 else w[:1].upper() + w[1:] for i, w in enumerate(words))
    raise ValueError(case)


def text_stats(text: str) -> Dict[str, int]:
    words = re.findall(r"[^\W_]+(?:[-'’][^\W_]+)*", text)
    sentences = [s for s in re.split(r"[.!?…]+(?:\s+|$)", text.strip()) if re.search(r"[^\W_]", s)]
    return {"words": len(words), "chars": len(text), "chars_no_spaces": len(re.sub(r"\s", "", text)),
            "lines": text.count("\n") + 1 if text else 0, "sentences": len(sentences),
            "paragraphs": len([p for p in re.split(r"\n\s*\n", text) if p.strip()]),
            "read_seconds": int(round(len(words) / 200 * 60))}       # ~200 words a minute


LOREM = ("lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor incididunt ut labore et dolore "
         "magna aliqua ut enim ad minim veniam quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo "
         "consequat duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur "
         "excepteur sint occaecat cupidatat non proident sunt in culpa qui officia deserunt mollit anim id est laborum").split()


def lorem(words: int = 0, paragraphs: int = 0) -> str:
    """Deterministic: always starts with «Lorem ipsum dolor sit amet»; sentences of 8–14 words."""
    if not words and not paragraphs:
        paragraphs = 1
    if words:
        return _sentences(max(1, min(2000, int(words))), 0)
    return "\n\n".join(_sentences(48 + (i % 3) * 8, i * 7) for i in range(max(1, min(20, int(paragraphs)))))


def _sentences(count: int, offset: int) -> str:
    out, sentence, n = [], [], 0
    for i in range(count):
        sentence.append(LOREM[(i + offset) % len(LOREM)])
        n += 1
        if n >= 8 + (len(out) * 3 + offset) % 7 or i == count - 1:
            text = " ".join(sentence)
            out.append(text[:1].upper() + text[1:] + ".")
            sentence, n = [], 0
    return " ".join(out)


# ================================================================== dates
_MONTHS = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "мая": 5, "май": 5, "июн": 6, "июл": 7, "авг": 8, "сен": 9, "окт": 10,
    "ноя": 11, "дек": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9,
    "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_RE = r"(?:январ\w*|феврал\w*|март\w*|апрел\w*|ма[яй]|июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*|" \
            r"jan\w*|feb\w*|mar\w*|apr\w*|may|jun\w*|jul\w*|aug\w*|sep\w*|oct\w*|nov\w*|dec\w*)"
WEEKDAYS = {"ru": ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"],
            "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]}


def parse_date(text: str, today: date, *, future: bool = True) -> Optional[date]:
    """A date in the forms people type. A date without a year is the next one (``future``) or this year's."""
    t = str(text or "").strip().lower().replace("ё", "е").strip(" ?.,")
    if re.fullmatch(r"(?:нового года|новый год|new year(?:'s)?(?: day)?)", t):
        return date(today.year + 1, 1, 1)
    t = re.sub(r"\s*(?:года|год|г\.?)$", "", t)
    if t in ("", "сегодня", "today", "сегодняшнего дня"):
        return today
    if t in ("завтра", "tomorrow"):
        return today + timedelta(days=1)
    if t in ("вчера", "yesterday"):
        return today - timedelta(days=1)
    if t in ("послезавтра", "day after tomorrow"):
        return today + timedelta(days=2)
    year: Optional[int] = None
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.fullmatch(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2}|\d{4}))?", t)
        if m:
            day, month = int(m.group(1)), int(m.group(2))
            year = _year(m.group(3))
        else:
            m = re.fullmatch(rf"(\d{{1,2}})\s+({_MONTH_RE})(?:\s+(\d{{4}}))?", t) or None
            if m:
                day, month, year = int(m.group(1)), _MONTHS.get(m.group(2)[:3], 0), _year(m.group(3))
            else:
                m = re.fullmatch(rf"({_MONTH_RE})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?", t)
                if not m:
                    return None
                month, day, year = _MONTHS.get(m.group(1)[:3], 0), int(m.group(2)), _year(m.group(3))
    try:
        if year is not None:
            return date(year, month, day)
        found = date(today.year, month, day)
        return found.replace(year=today.year + 1) if future and found < today else found
    except ValueError:
        return None


def _year(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    n = int(value)
    return 2000 + n if n < 100 else n


def add_to_date(start: date, amount: int, unit: str) -> date:
    if unit == "weeks":
        return start + timedelta(weeks=amount)
    if unit in ("months", "years"):
        months = amount * (12 if unit == "years" else 1)
        y, m = divmod(start.month - 1 + months, 12)
        year, month = start.year + y, m + 1
        return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))
    return start + timedelta(days=amount)


def plural_ru(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def fmt_date(d: date, lang: str) -> str:
    if lang == "en":
        return f"{WEEKDAYS['en'][d.weekday()]}, {calendar.month_name[d.month]} {d.day}, {d.year}"
    months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
    return f"{d.day} {months[d.month - 1]} {d.year}, {WEEKDAYS['ru'][d.weekday()]}"


def days_text(n: int, lang: str) -> str:
    return f"{n} " + (("day" if abs(n) == 1 else "days") if lang == "en" else plural_ru(n, "день", "дня", "дней"))


# ================================================================== time zones
# (standard UTC offset in minutes, DST rule) — rule: None, "eu" (last Sun Mar → last Sun Oct, 01:00 UTC),
# "us" (2nd Sun Mar → 1st Sun Nov, 02:00 local), "au" (southern: 1st Sun Oct → 1st Sun Apr, 02:00 local),
# "nz" (last Sun Sep → 1st Sun Apr). Offline on purpose: Windows Python has no tz database without ``tzdata``.
ZONES: Dict[str, Tuple[int, Optional[str], str, str]] = {
    # key: (offset, rule, Russian name, English name)
    "moscow": (180, None, "Москва", "Moscow"), "spb": (180, None, "Санкт-Петербург", "Saint Petersburg"),
    "kaliningrad": (120, None, "Калининград", "Kaliningrad"), "samara": (240, None, "Самара", "Samara"),
    "yekaterinburg": (300, None, "Екатеринбург", "Yekaterinburg"), "omsk": (360, None, "Омск", "Omsk"),
    "novosibirsk": (420, None, "Новосибирск", "Novosibirsk"), "krasnoyarsk": (420, None, "Красноярск", "Krasnoyarsk"),
    "irkutsk": (480, None, "Иркутск", "Irkutsk"), "yakutsk": (540, None, "Якутск", "Yakutsk"),
    "vladivostok": (600, None, "Владивосток", "Vladivostok"), "magadan": (660, None, "Магадан", "Magadan"),
    "kamchatka": (720, None, "Петропавловск-Камчатский", "Petropavlovsk-Kamchatsky"),
    "minsk": (180, None, "Минск", "Minsk"), "kyiv": (120, "eu", "Киев", "Kyiv"), "astana": (300, None, "Астана", "Astana"),
    "almaty": (300, None, "Алматы", "Almaty"), "tashkent": (300, None, "Ташкент", "Tashkent"),
    "bishkek": (360, None, "Бишкек", "Bishkek"), "tbilisi": (240, None, "Тбилиси", "Tbilisi"),
    "yerevan": (240, None, "Ереван", "Yerevan"), "baku": (240, None, "Баку", "Baku"),
    "vilnius": (120, "eu", "Вильнюс", "Vilnius"), "riga": (120, "eu", "Рига", "Riga"), "tallinn": (120, "eu", "Таллин", "Tallinn"),
    "warsaw": (60, "eu", "Варшава", "Warsaw"), "berlin": (60, "eu", "Берлин", "Berlin"), "paris": (60, "eu", "Париж", "Paris"),
    "rome": (60, "eu", "Рим", "Rome"), "madrid": (60, "eu", "Мадрид", "Madrid"), "prague": (60, "eu", "Прага", "Prague"),
    "vienna": (60, "eu", "Вена", "Vienna"), "amsterdam": (60, "eu", "Амстердам", "Amsterdam"),
    "stockholm": (60, "eu", "Стокгольм", "Stockholm"), "helsinki": (120, "eu", "Хельсинки", "Helsinki"),
    "athens": (120, "eu", "Афины", "Athens"), "london": (0, "eu", "Лондон", "London"), "lisbon": (0, "eu", "Лиссабон", "Lisbon"),
    "dublin": (0, "eu", "Дублин", "Dublin"), "istanbul": (180, None, "Стамбул", "Istanbul"),
    "dubai": (240, None, "Дубай", "Dubai"), "tel_aviv": (120, "il", "Тель-Авив", "Tel Aviv"),
    "cairo": (120, None, "Каир", "Cairo"), "delhi": (330, None, "Дели", "Delhi"), "mumbai": (330, None, "Мумбаи", "Mumbai"),
    "bangkok": (420, None, "Бангкок", "Bangkok"), "jakarta": (420, None, "Джакарта", "Jakarta"),
    "beijing": (480, None, "Пекин", "Beijing"), "shanghai": (480, None, "Шанхай", "Shanghai"),
    "hong_kong": (480, None, "Гонконг", "Hong Kong"), "singapore": (480, None, "Сингапур", "Singapore"),
    "seoul": (540, None, "Сеул", "Seoul"), "tokyo": (540, None, "Токио", "Tokyo"),
    "sydney": (600, "au", "Сидней", "Sydney"), "melbourne": (600, "au", "Мельбурн", "Melbourne"),
    "auckland": (720, "nz", "Окленд", "Auckland"), "new_york": (-300, "us", "Нью-Йорк", "New York"),
    "washington": (-300, "us", "Вашингтон", "Washington"), "toronto": (-300, "us", "Торонто", "Toronto"),
    "miami": (-300, "us", "Майами", "Miami"), "chicago": (-360, "us", "Чикаго", "Chicago"),
    "denver": (-420, "us", "Денвер", "Denver"), "los_angeles": (-480, "us", "Лос-Анджелес", "Los Angeles"),
    "san_francisco": (-480, "us", "Сан-Франциско", "San Francisco"), "seattle": (-480, "us", "Сиэтл", "Seattle"),
    "vancouver": (-480, "us", "Ванкувер", "Vancouver"), "mexico_city": (-360, None, "Мехико", "Mexico City"),
    "sao_paulo": (-180, None, "Сан-Паулу", "São Paulo"), "buenos_aires": (-180, None, "Буэнос-Айрес", "Buenos Aires"),
    "utc": (0, None, "UTC", "UTC"),
}
#: Spoken forms → zone key. Russian stems cover the cases («в Лондоне», «по Москве», «из Токио»).
_CITY_WORDS: List[Tuple[str, str]] = [
    ("москв", "moscow"), ("мск", "moscow"), ("moscow", "moscow"), ("msk", "moscow"), ("питер", "spb"), ("петербург", "spb"),
    ("спб", "spb"), ("st petersburg", "spb"), ("saint petersburg", "spb"), ("калининград", "kaliningrad"),
    ("kaliningrad", "kaliningrad"), ("самар", "samara"), ("samara", "samara"), ("екатеринбург", "yekaterinburg"),
    ("екб", "yekaterinburg"), ("yekaterinburg", "yekaterinburg"), ("омск", "omsk"), ("omsk", "omsk"),
    ("новосибирск", "novosibirsk"), ("novosibirsk", "novosibirsk"), ("красноярск", "krasnoyarsk"),
    ("krasnoyarsk", "krasnoyarsk"), ("иркутск", "irkutsk"), ("irkutsk", "irkutsk"), ("якутск", "yakutsk"),
    ("yakutsk", "yakutsk"), ("владивосток", "vladivostok"), ("vladivostok", "vladivostok"), ("магадан", "magadan"),
    ("magadan", "magadan"), ("камчатк", "kamchatka"), ("петропавловск", "kamchatka"), ("kamchatka", "kamchatka"),
    ("минск", "minsk"), ("minsk", "minsk"), ("киев", "kyiv"), ("кие", "kyiv"), ("kyiv", "kyiv"), ("kiev", "kyiv"),
    ("астан", "astana"), ("astana", "astana"), ("алмат", "almaty"), ("almaty", "almaty"), ("ташкент", "tashkent"),
    ("tashkent", "tashkent"), ("бишкек", "bishkek"), ("bishkek", "bishkek"), ("тбилиси", "tbilisi"), ("tbilisi", "tbilisi"),
    ("ереван", "yerevan"), ("yerevan", "yerevan"), ("баку", "baku"), ("baku", "baku"), ("вильнюс", "vilnius"),
    ("vilnius", "vilnius"), ("рига", "riga"), ("риге", "riga"), ("riga", "riga"), ("таллин", "tallinn"), ("tallinn", "tallinn"),
    ("варшав", "warsaw"), ("warsaw", "warsaw"), ("берлин", "berlin"), ("berlin", "berlin"), ("париж", "paris"),
    ("paris", "paris"), ("рим", "rome"), ("rome", "rome"), ("мадрид", "madrid"), ("madrid", "madrid"), ("праг", "prague"),
    ("prague", "prague"), ("вен", "vienna"), ("vienna", "vienna"), ("амстердам", "amsterdam"), ("amsterdam", "amsterdam"),
    ("стокгольм", "stockholm"), ("stockholm", "stockholm"), ("хельсинки", "helsinki"), ("helsinki", "helsinki"),
    ("афин", "athens"), ("athens", "athens"), ("лондон", "london"), ("london", "london"), ("лиссабон", "lisbon"),
    ("lisbon", "lisbon"), ("дублин", "dublin"), ("dublin", "dublin"), ("стамбул", "istanbul"), ("istanbul", "istanbul"),
    ("дуба", "dubai"), ("dubai", "dubai"), ("тель-авив", "tel_aviv"), ("тель авив", "tel_aviv"), ("tel aviv", "tel_aviv"),
    ("израил", "tel_aviv"), ("каир", "cairo"), ("cairo", "cairo"), ("дели", "delhi"), ("delhi", "delhi"),
    ("мумба", "mumbai"), ("mumbai", "mumbai"), ("бангкок", "bangkok"), ("bangkok", "bangkok"), ("джакарт", "jakarta"),
    ("jakarta", "jakarta"), ("пекин", "beijing"), ("beijing", "beijing"), ("китай", "beijing"), ("шанха", "shanghai"),
    ("shanghai", "shanghai"), ("гонконг", "hong_kong"), ("hong kong", "hong_kong"), ("сингапур", "singapore"),
    ("singapore", "singapore"), ("сеул", "seoul"), ("seoul", "seoul"), ("токио", "tokyo"), ("tokyo", "tokyo"),
    ("япони", "tokyo"), ("сидне", "sydney"), ("sydney", "sydney"), ("мельбурн", "melbourne"), ("melbourne", "melbourne"),
    ("окленд", "auckland"), ("auckland", "auckland"), ("нью-йорк", "new_york"), ("нью йорк", "new_york"),
    ("new york", "new_york"), ("nyc", "new_york"), ("вашингтон", "washington"), ("washington", "washington"),
    ("торонто", "toronto"), ("toronto", "toronto"), ("майами", "miami"), ("miami", "miami"), ("чикаго", "chicago"),
    ("chicago", "chicago"), ("денвер", "denver"), ("denver", "denver"), ("лос-анджелес", "los_angeles"),
    ("лос анджелес", "los_angeles"), ("los angeles", "los_angeles"), ("la", "los_angeles"),
    ("сан-франциско", "san_francisco"), ("сан франциско", "san_francisco"), ("san francisco", "san_francisco"),
    ("сиэтл", "seattle"), ("seattle", "seattle"), ("ванкувер", "vancouver"), ("vancouver", "vancouver"),
    ("мехико", "mexico_city"), ("mexico city", "mexico_city"), ("сан-паулу", "sao_paulo"), ("sao paulo", "sao_paulo"),
    ("são paulo", "sao_paulo"), ("буэнос-айрес", "buenos_aires"), ("buenos aires", "buenos_aires"),
    ("utc", "utc"), ("gmt", "utc"), ("гринвич", "utc"),
]


def zone_key(text: str) -> str:
    t = re.sub(r"\s+", " ", str(text or "").strip().lower().replace("ё", "е")).strip(" ?.,")
    t = re.sub(r"^(?:в|во|по|из|in|at|from)\s+", "", t)
    t = re.sub(r"\s+(?:time|времени|время)$", "", t)
    if not t:
        return ""
    for word, key in _CITY_WORDS:              # whole phrase or its Russian case form (stem + ending ≤ 3)
        if t == word or (len(word) >= 3 and re.fullmatch(r"[а-я]", word[0]) and t.startswith(word) and len(t) - len(word) <= 3):
            return key
    return ""


def _nth_sunday(year: int, month: int, n: int) -> date:
    """n-th Sunday of the month (n = -1 → the last)."""
    if n < 0:
        last = date(year, month, calendar.monthrange(year, month)[1])
        return last - timedelta(days=(last.weekday() - 6) % 7)
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def utc_offset(key: str, at_utc: datetime) -> int:
    """UTC offset of the zone in minutes at the UTC instant ``at_utc`` (naive, UTC)."""
    base, rule, _ru, _en = ZONES[key]
    y = at_utc.year
    if rule == "eu":
        start = datetime.combine(_nth_sunday(y, 3, -1), datetime.min.time()) + timedelta(hours=1)
        end = datetime.combine(_nth_sunday(y, 10, -1), datetime.min.time()) + timedelta(hours=1)
        return base + (60 if start <= at_utc < end else 0)
    if rule == "il":          # Friday before the last Sunday of March → last Sunday of October (02:00 local)
        start = datetime.combine(_nth_sunday(y, 3, -1) - timedelta(days=2), datetime.min.time()) + timedelta(hours=2) - timedelta(minutes=base)
        end = datetime.combine(_nth_sunday(y, 10, -1), datetime.min.time()) + timedelta(hours=2) - timedelta(minutes=base + 60)
        return base + (60 if start <= at_utc < end else 0)
    if rule == "us":
        start = datetime.combine(_nth_sunday(y, 3, 2), datetime.min.time()) + timedelta(hours=2) - timedelta(minutes=base)
        end = datetime.combine(_nth_sunday(y, 11, 1), datetime.min.time()) + timedelta(hours=2) - timedelta(minutes=base + 60)
        return base + (60 if start <= at_utc < end else 0)
    if rule in ("au", "nz"):  # southern hemisphere: summer time spans the new year
        end = datetime.combine(_nth_sunday(y, 4, 1), datetime.min.time()) + timedelta(hours=3) - timedelta(minutes=base + 60)
        start_day = _nth_sunday(y, 10, 1) if rule == "au" else _nth_sunday(y, 9, -1)
        start = datetime.combine(start_day, datetime.min.time()) + timedelta(hours=2) - timedelta(minutes=base)
        return base + (60 if at_utc < end or at_utc >= start else 0)
    return base


def zone_name(key: str, lang: str) -> str:
    return ZONES[key][3 if lang == "en" else 2]


def fmt_offset(minutes: int) -> str:
    sign = "+" if minutes >= 0 else "−"
    h, m = divmod(abs(minutes), 60)
    return f"UTC{sign}{h}" + (f":{m:02d}" if m else "")


def parse_clock(text: str) -> Optional[Tuple[int, int]]:
    t = str(text or "").strip().lower().replace(".", ":")
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|утра|дня|вечера|ночи)?", t)
    if not m:
        return None
    h, mi, mer = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if mer in ("pm", "дня", "вечера") and h < 12:
        h += 12
    if mer in ("am", "ночи", "утра") and h == 12:
        h = 0
    if h > 23 or mi > 59 or (m.group(2) is None and mer is None):
        return None
    return h, mi


# ================================================================== phrases
def install() -> None:
    """Идемпотентно добавить действия и фразы (категория «tools» объявлена в ``assistant_catalog.CATEGORIES``)."""
    for row in NEW_ACTIONS:
        if row["id"] not in catalog.BY_ID:
            catalog.ACTIONS.append(row)
            catalog.BY_ID[row["id"]] = row
    try:
        from . import assistant_intents as intents
    except Exception:
        return
    intents._QUESTION_OK.update(QUESTION_OK)
    if any(name.startswith("r25.") for name, _p, _f in intents._RULES):
        return
    clip = r"(?:(?:из\s+)?буфер\w*(?:\s+обмена)?|(?:from\s+)?(?:the\s+)?clipboard)"
    rules = [
        ("r25.color", re.compile(r"^(?:(?:цвет|color|colou?r|конвертируй|переведи|convert)\s+)?"
                                 r"(#[0-9a-f]{3,8}|rgba?\s*\([^)]*\)|hsla?\s*\([^)]*\))"
                                 r"(?:\s+(?:в|во|to|in|into)\s+(?:hex|rgb|hsl|rgba|hsla))?$", re.I), _color),
        ("r25.encode", re.compile(r"^(?:(закодируй|кодируй|encode|раскодируй|декодируй|decode)\s+(?:в\s+|to\s+|из\s+|from\s+)?)?"
                                  r"(base\s?64|b64|url)[\s-]*(encode|decode|кодир\w*|закодир\w*|декодир\w*|раскодир\w*)?"
                                  r"(?:\s*[:—-]\s*|\s+)?(.*)$", re.I), _encode),
        ("r25.json", re.compile(r"^(?:(отформатируй|форматируй|красиво|проверь|валидируй|сожми|минифицируй|"
                                r"format|pretty(?:-?print)?|beautify|validate|check|minify|compact)\s+)?json\b\s*[:—-]?\s*(.*)$", re.I), _json),
        ("r25.hash", re.compile(r"^(?:(?:посчитай|вычисли|calc(?:ulate)?|compute)\s+)?(?:(?:хеш|хэш|hash)\s+)?"
                                r"(md5|sha-?1|sha-?256|sha-?384|sha-?512)(?:\s+(?:от|of|для|for))?\s*[:—-]?\s*(.*)$", re.I), _hash),
        ("r25.case", re.compile(r"^(?:(?:переведи|преобразуй|сделай|convert|make|to)\s+)?(?:(?:в|to|in)\s+)?"
                                r"(верхн\w*\s+регистр\w*|нижн\w*\s+регистр\w*|заглавн\w*(?:\s+букв\w*)?|прописн\w*(?:\s+букв\w*)?|"
                                r"строчн\w*(?:\s+букв\w*)?|upper\s?case|lower\s?case|title\s?case|sentence\s?case|"
                                r"snake[_ ]?case|camel[_ ]?case|kebab[-_ ]?case|транслит\w*|translit\w*)\s*[:—-]?\s*(.*)$", re.I), _case),
        ("r25.stats", re.compile(r"^(?:(?:сколько|посчитай|подсчитай)\s+(?:слов|символов|знаков|букв|строк)(?:\s+и\s+(?:слов|символов|знаков|строк))?|"
                                 r"статистика\s+текста|count\s+(?:the\s+)?(?:words|characters|chars)|word\s+count|character\s+count|"
                                 r"how\s+many\s+(?:words|characters))(?:\s+(?:в|in))?\s*(?:(" + clip + r"|тексте|text|this)\s*)?([:—-]\s*)?(.*)$", re.I), _stats),
        ("r25.days_until", re.compile(r"^(?:сколько\s+(?:осталось\s+)?дней\s+(?:осталось\s+)?до|how\s+many\s+days\s+(?:until|till|to|left\s+until)|days\s+(?:until|till|to))\s+(.+)$", re.I), _days_until),
        ("r25.days_between", re.compile(r"^(?:сколько\s+дней\s+)?(?:между|с|from|days\s+between|between)\s+(.+?)\s+(?:и|по|до|and|to|till)\s+(.+)$", re.I), _days_between),
        ("r25.weekday", re.compile(r"^(?:какой\s+)?день\s+недели\s+(?:будет\s+|был\s+)?(.+)$|^what\s+(?:day(?:\s+of\s+the\s+week)?)\s+(?:is|was|will\s+be)\s+(.+?)(?:\s+be)?$|^weekday\s+(?:of\s+)?(.+)$", re.I), _weekday),
        ("r25.date_add", re.compile(r"^(?:(?:какая|какое)\s+(?:будет\s+)?(?:дата|число)(?:\s+будет)?\s+|дата\s+|date\s+)?"
                                    r"(через|in|\+|-|минус|plus|minus)?\s*(\d{1,5})\s*(дн\w*|день|сут\w*|недел\w*|месяц\w*|год\w*|лет|days?|weeks?|months?|years?)"
                                    r"\s*(назад|ago|from\s+now|from\s+today|later|спустя)?(?:\s+(?:от|после|from|after)\s+(.+?))?"
                                    r"(?:\s+(?:какая|какое)\s+(?:будет\s+)?(?:дата|число))?$", re.I), _date_add),
        ("r25.tz_now", re.compile(r"^(?:сколько\s+(?:сейчас\s+)?времени|который\s+(?:сейчас\s+)?час|(?:какое\s+)?(?:сейчас\s+)?время|"
                                  r"what\s+time\s+is\s+it|(?:current\s+|local\s+)?time)\s+(?:сейчас\s+)?(?:в|во|in|at)\s+(.+?)(?:\s+(?:сейчас|now))?$", re.I), _tz_now),
        ("r25.tz_convert", re.compile(r"^(?:(?:переведи|сконвертируй|convert)\s+)?(\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm|утра|дня|вечера|ночи)?)\s+"
                                      r"(?:по\s+)?(.+?)\s+(?:в|во|на|to|in|into)\s+(.+?)(?:\s+time)?$", re.I), _tz_convert),
        ("r25.coin", re.compile(r"^(?:(?:брось|подбрось|кинь|подкинь|flip|toss)\s+(?:a\s+)?(?:монет\w*|монетк\w*|coin)|орел\s+или\s+решка|орёл\s+или\s+решка|heads\s+or\s+tails)$", re.I),
         lambda m, ctx: ("random.pick", {"kind": "coin"})),
        ("r25.dice", re.compile(r"^(?:брось|кинь|подбрось|roll|throw)\s+(?:(\d{1,2})\s+)?(?:a\s+)?(?:кубик\w*|кост(?:ь|и|ей)|dic?e)$|"
                                r"^(?:(?:брось|кинь|roll)\s+)?(\d{1,2})?d(\d{1,4})$", re.I), _dice),
        ("r25.number", re.compile(r"^(?:(?:загадай|выбери|дай|сгенерируй|generate|pick|give\s+me)\s+(?:a\s+)?)?(?:случайное\s+число|рандомное\s+число|random\s+(?:number|integer))"
                                  r"(?:\s+(?:от|from|between)\s+(-?\d+)\s+(?:до|to|and|-)\s+(-?\d+)|\s+(-?\d+)\s*-\s*(-?\d+)|\s+(?:до|to|up\s+to)\s+(-?\d+))?$", re.I), _number),
        ("r25.choice", re.compile(r"^(?:выбери\s+(?:случайно|наугад|рандомно|одно|один|одну)?|случайный\s+выбор|pick\s+(?:one|randomly|at\s+random)?|choose\s+(?:one|randomly)?)"
                                  r"\s*(?:из|from|between|of)?\s*[:—-]?\s*(.+)$", re.I), _choice),
        ("r25.lorem", re.compile(r"^(?:(?:сгенерируй|создай|дай|напиши|generate|give\s+me)\s+)?(?:текст[-\s]+рыб\w*|рыб\w*\s+текст\w*|lorem(?:\s+ipsum)?|лорем(?:\s+ипсум)?)"
                                 r"(?:\s+(?:на|в|of|with))?(?:\s+(\d{1,4})\s*(слов\w*|words?|абзац\w*|paragraphs?|параграф\w*))?$", re.I), _lorem),
    ]
    intents._RULES[0:0] = rules


def _clip_text(value: Optional[str]) -> str:
    """The text after the command; «буфер»/«clipboard» (or nothing) means the clipboard → ''."""
    text = str(value or "").strip()
    if re.fullmatch(r"(?:(?:из|с|в|from|of|the|in)\s+)?(?:буфер\w*(?:\s+обмена)?|clipboard|скопированное|this|это)", text, re.I):
        return ""
    return text


def _color(m: "re.Match[str]", ctx: Any):
    return ("color.convert", {"color": m.group(1)}) if parse_color(m.group(1)) else None


def _encode(m: "re.Match[str]", ctx: Any):
    verb, codec, suffix, rest = (m.group(i) or "" for i in (1, 2, 3, 4))
    codec = "url" if codec.lower() == "url" else "base64"
    word = (verb + " " + suffix).lower()
    op = "decode" if re.search(r"раскод|декод|decode", word) else ("encode" if word.strip() else "auto")
    text = _clip_text(rest)
    if codec == "url" and op == "auto":
        return None                        # «url https://…» is not a request to encode
    if not word.strip() and not text and not re.search(r"буфер|clipboard", rest, re.I):
        return None                        # a bare «base64» is a question for the model; «base64 буфер» is not
    return "text.encode", {"codec": codec, "op": op, **({"text": text} if text else {})}


def _json(m: "re.Match[str]", ctx: Any):
    verb, rest = (m.group(1) or "").lower(), m.group(2) or ""
    text = _clip_text(rest)
    if not verb and not text.startswith(("{", "[")) and not re.search(r"буфер|clipboard", rest, re.I):
        return None
    if verb and text and not text.startswith(("{", "[", '"')) and not re.fullmatch(r"-?\d.*|true|false|null", text):
        return None                        # «проверь json схему для…» is a question
    mode = "minify" if re.match(r"сожм|миниф|minify|compact", verb) else ("validate" if re.match(r"провер|валид|validate|check", verb) else "pretty")
    return "json.format", {"mode": mode, **({"text": text} if text else {})}


def _hash(m: "re.Match[str]", ctx: Any):
    algo = m.group(1).lower().replace("-", "")
    return "hash.text", {"algo": algo, **({"text": _clip_text(m.group(2))} if _clip_text(m.group(2)) else {})}


def _case(m: "re.Match[str]", ctx: Any):
    word = re.sub(r"\s+", " ", m.group(1).lower())
    case = ("upper" if re.match(r"верхн|заглавн|прописн|upper", word) else "lower" if re.match(r"нижн|строчн|lower", word)
            else "title" if word.startswith("title") else "sentence" if word.startswith("sentence")
            else "snake" if word.startswith("snake") else "camel" if word.startswith("camel")
            else "kebab" if word.startswith("kebab") else "translit")
    text = _clip_text(m.group(2))
    return "text.case", {"case": case, **({"text": text} if text else {})}


def _stats(m: "re.Match[str]", ctx: Any):
    text = _clip_text(m.group(3))
    if text and not m.group(2) and not re.match(r"[«\"'“]", text):
        return None                        # «сколько слов в романе …» is a question for the model, not a text to count
    return "text.stats", ({"text": text} if text else {})


def _today(ctx: Any) -> date:
    now = getattr(ctx, "now", None)
    return now.date() if isinstance(now, datetime) else date.today()


def _days_until(m: "re.Match[str]", ctx: Any):
    return ("date.calc", {"op": "until", "date": m.group(1).strip()}) if parse_date(m.group(1), _today(ctx)) else None


def _days_between(m: "re.Match[str]", ctx: Any):
    today = _today(ctx)
    a, b = m.group(1).strip(), m.group(2).strip()
    if not parse_date(a, today, future=False) or not parse_date(b, today, future=False):
        return None
    return "date.calc", {"op": "between", "date": a, "date2": b}


def _weekday(m: "re.Match[str]", ctx: Any):
    value = next((g for g in m.groups() if g), "").strip()
    return ("date.calc", {"op": "weekday", "date": value}) if parse_date(value, _today(ctx), future=False) else None


def _date_add(m: "re.Match[str]", ctx: Any):
    lead, num, unit_word, tail, base = m.group(1), m.group(2), m.group(3).lower(), (m.group(4) or "").lower(), m.group(5)
    text = ctx.low if hasattr(ctx, "low") else m.group(0).lower()
    # Only an explicit date question: «через 30 дней» alone is the start of a reminder/timer phrase.
    if not (re.search(r"дата|число|date|какое|какая", text) or lead in ("+", "-", "минус", "plus", "minus")
            or re.search(r"назад|ago|from now|from today|спустя", tail) or base):
        return None
    amount = int(num) * (-1 if lead in ("-", "минус", "minus") or tail in ("назад", "ago") else 1)
    unit = ("weeks" if re.match(r"недел|week", unit_word) else "months" if re.match(r"месяц|month", unit_word)
            else "years" if re.match(r"год|лет|year", unit_word) else "days")
    params: Dict[str, Any] = {"op": "add", "amount": amount, "unit": unit}
    if base:
        if not parse_date(base, _today(ctx), future=False):
            return None
        params["date"] = base.strip()
    return "date.calc", params


def _tz_now(m: "re.Match[str]", ctx: Any):
    key = zone_key(m.group(1))
    return ("time.zone", {"city": key}) if key else None


def _tz_convert(m: "re.Match[str]", ctx: Any):
    clock, src, dst = m.group(1), m.group(2), m.group(3)
    if not parse_clock(clock):
        return None
    a, b = zone_key(src), zone_key(dst)
    if not a or not b:
        return None
    return "time.zone", {"time": clock.strip(), "from": a, "city": b}


def _dice(m: "re.Match[str]", ctx: Any):
    count, sides = m.group(1) or m.group(2), m.group(3)
    return "random.pick", {"kind": "dice", "count": int(count or 1), "sides": int(sides or 6)}


def _number(m: "re.Match[str]", ctx: Any):
    lo, hi = m.group(1) or m.group(3), m.group(2) or m.group(4)
    if m.group(5):
        lo, hi = "1", m.group(5)
    return "random.pick", {"kind": "number", "min": int(lo or 1), "max": int(hi or 100)}


def _choice(m: "re.Match[str]", ctx: Any):
    if not re.search(r"случайн|наугад|рандом|random|\bone\b|:", m.group(0), re.I):
        return None                        # «выбери файл, папку» is not a lottery
    items = [x.strip() for x in re.split(r",|;|\s+или\s+|\s+or\s+", m.group(1)) if x.strip()]
    return ("random.pick", {"kind": "choice", "items": ", ".join(items)}) if len(items) >= 2 else None


def _lorem(m: "re.Match[str]", ctx: Any):
    n, unit = m.group(1), (m.group(2) or "").lower()
    if not n:
        return "lorem.generate", {"paragraphs": 1}
    return "lorem.generate", ({"words": int(n)} if re.match(r"слов|word", unit) else {"paragraphs": int(n)})


# ================================================================== handlers
def bind(eng: Any) -> None:
    from .assistant_commands import ActionError, tr

    async def clip_get() -> str:
        try:
            return str(await eng._blocking(eng.backend.clipboard_get) or "")
        except Exception:
            return ""

    async def clip_set(text: str) -> bool:
        try:
            await eng._blocking(eng.backend.clipboard_set, text)
            return True
        except Exception:
            return False

    async def text_or_clip(params: Dict[str, Any], lang: str) -> str:
        text = str(params.get("text") or "")
        if not text.strip():
            text = await clip_get()
        if not text.strip():
            raise ActionError(tr(lang, "Нечего обрабатывать: буфер обмена пуст. Скопируйте текст или напишите его после команды.",
                                 "Nothing to process: the clipboard is empty. Copy text or type it after the command."), "empty")
        return text[:200_000]

    def copied_note(ok: bool, lang: str) -> List[List[str]]:
        return [[tr(lang, "Буфер", "Clipboard"), tr(lang, "результат скопирован", "result copied")]] if ok else []

    async def h_color(params: Dict[str, Any], lang: str = "ru", **_k: Any) -> Dict[str, Any]:
        parsed = parse_color(str(params.get("color") or ""))
        if not parsed:
            raise ActionError(tr(lang, "Не понял цвет. Примеры: #ff8800, rgb(255, 136, 0), hsl(32, 100%, 50%).",
                                 "Unknown color. Examples: #ff8800, rgb(255, 136, 0), hsl(32, 100%, 50%)."), "bad_params")
        forms = color_forms(*parsed)
        return {"message": forms["hex"], "copy": forms["hex"], "swatch": forms["hex"][:7],
                "details": [["HEX", forms["hex"]], ["RGB", forms["rgb"]], ["HSL", forms["hsl"]]]}

    async def h_encode(params: Dict[str, Any], lang: str = "ru", **_k: Any) -> Dict[str, Any]:
        text = await text_or_clip(params, lang)
        codec = params.get("codec") or "base64"
        try:
            out, op = encode_text(text, codec, params.get("op") or "auto")
        except ValueError as exc:
            raise ActionError(tr(lang, "Не получилось раскодировать: это не Base64." if str(exc) == "not_base64" else
                                 "Не получилось раскодировать: внутри Base64 не текст UTF-8.",
                                 "Could not decode: not Base64." if str(exc) == "not_base64" else
                                 "Could not decode: the Base64 data is not UTF-8 text."), "bad_input")
        ok = await clip_set(out)
        name = "Base64" if codec == "base64" else "URL"
        how = tr(lang, "закодировано", "encoded") if op == "encode" else tr(lang, "раскодировано", "decoded")
        return {"message": out, "kind": "code", "copy": out,
                "details": [[name, how]] + copied_note(ok, lang)}

    async def h_json(params: Dict[str, Any], lang: str = "ru", **_k: Any) -> Dict[str, Any]:
        text = (await text_or_clip(params, lang)).strip().lstrip("\ufeff")
        mode = params.get("mode") or "pretty"
        try:
            out = format_json(text, "minify" if mode == "minify" else "pretty")
        except json.JSONDecodeError as exc:
            raise ActionError(tr(lang, f"JSON с ошибкой — строка {exc.lineno}, столбец {exc.colno}: {exc.msg}",
                                 f"Invalid JSON — line {exc.lineno}, column {exc.colno}: {exc.msg}"), "bad_input") from None
        if mode == "validate":
            data = json.loads(text)
            kind = type(data).__name__
            size = len(data) if isinstance(data, (list, dict)) else 1
            what = {"dict": tr(lang, "объект", "object"), "list": tr(lang, "массив", "array")}.get(kind, tr(lang, "значение", "value"))
            return {"message": tr(lang, f"JSON корректный: {what}, элементов — {size}.", f"Valid JSON: {what}, {size} item(s).")}
        ok = await clip_set(out)
        return {"message": out[:20_000], "kind": "code", "copy": out,
                "details": [[tr(lang, "Размер", "Size"), f"{len(text)} → {len(out)}"]] + copied_note(ok, lang)}

    async def h_hash(params: Dict[str, Any], lang: str = "ru", **_k: Any) -> Dict[str, Any]:
        text = await text_or_clip(params, lang)
        algo = params.get("algo") or "sha256"
        digest = hash_text(text, algo)
        ok = await clip_set(digest)
        return {"message": digest, "kind": "code", "copy": digest,
                "details": [[algo.upper().replace("SHA", "SHA-"), tr(lang, f"{len(text)} симв., UTF-8", f"{len(text)} chars, UTF-8")]]
                + copied_note(ok, lang)}

    async def h_case(params: Dict[str, Any], lang: str = "ru", **_k: Any) -> Dict[str, Any]:
        text = await text_or_clip(params, lang)
        out = change_case(text, params.get("case") or "upper")
        ok = await clip_set(out)
        return {"message": out[:20_000], "kind": "text", "copy": out, "details": copied_note(ok, lang)}

    async def h_stats(params: Dict[str, Any], lang: str = "ru", **_k: Any) -> Dict[str, Any]:
        text = await text_or_clip(params, lang)
        s = text_stats(text)
        mins, secs = divmod(s["read_seconds"], 60)
        read = (f"{mins} {tr(lang, 'мин', 'min')} " if mins else "") + f"{secs} {tr(lang, 'с', 's')}"
        msg = (f"{s['words']} " + (("word" if s["words"] == 1 else "words") if lang == "en" else plural_ru(s["words"], "слово", "слова", "слов"))
               + f" · {s['chars']} " + (("character" if s["chars"] == 1 else "characters") if lang == "en" else plural_ru(s["chars"], "символ", "символа", "символов")))
        return {"message": msg, "copy": str(s["words"]), "details": [
            [tr(lang, "Слов", "Words"), str(s["words"])], [tr(lang, "Символов", "Characters"), str(s["chars"])],
            [tr(lang, "Без пробелов", "Without spaces"), str(s["chars_no_spaces"])], [tr(lang, "Строк", "Lines"), str(s["lines"])],
            [tr(lang, "Предложений", "Sentences"), str(s["sentences"])], [tr(lang, "Абзацев", "Paragraphs"), str(s["paragraphs"])],
            [tr(lang, "Чтение", "Reading time"), read]]}

    async def h_date(params: Dict[str, Any], lang: str = "ru", **_k: Any) -> Dict[str, Any]:
        today = eng.now_fn().date()
        op = params.get("op")
        bad = ActionError(tr(lang, "Не понял дату. Примеры: 31.12.2026, 2026-12-31, 9 мая, May 9 2027.",
                             "Unknown date. Examples: 2026-12-31, 31.12.2026, May 9, 9 мая 2027."), "bad_params")
        if op == "until" and not str(params.get("date") or "").strip():
            raise ActionError(tr(lang, "До какой даты считать? Пример: «сколько дней до 31 декабря».",
                                 "Until which date? Example: “days until December 31”."), "bad_params")
        first = parse_date(str(params.get("date") or ""), today, future=op == "until")
        if first is None:
            raise bad
        if op == "until":
            n = (first - today).days
            msg = (tr(lang, "Это сегодня", "That is today") if n == 0 else
                   tr(lang, f"До {fmt_date(first, lang)} — {days_text(n, lang)}", f"{days_text(n, lang)} until {fmt_date(first, lang)}") if n > 0 else
                   tr(lang, f"{fmt_date(first, lang)} было {days_text(-n, lang)} назад", f"{fmt_date(first, lang)} was {days_text(-n, lang)} ago"))
            return {"message": msg, "copy": str(abs(n)), "details": [[tr(lang, "Недель", "Weeks"), f"{abs(n) / 7:.1f}".rstrip("0").rstrip(".")]]}
        if op == "between":
            second = parse_date(str(params.get("date2") or ""), today, future=False)
            if second is None:
                raise bad
            n = abs((second - first).days)
            return {"message": tr(lang, f"Между датами {days_text(n, lang)}", f"{days_text(n, lang)} between the dates"), "copy": str(n),
                    "details": [[tr(lang, "С", "From"), fmt_date(min(first, second), lang)], [tr(lang, "По", "To"), fmt_date(max(first, second), lang)],
                                [tr(lang, "Включая обе даты", "Counting both ends"), days_text(n + 1, lang)]]}
        if op == "add":
            try:
                result = add_to_date(first, int(params.get("amount") or 0), params.get("unit") or "days")
            except (OverflowError, ValueError):
                raise bad from None
            return {"message": fmt_date(result, lang), "copy": result.strftime("%d.%m.%Y" if lang != "en" else "%Y-%m-%d"),
                    "details": [[tr(lang, "От", "From"), fmt_date(first, lang)]]}
        return {"message": fmt_date(first, lang), "copy": WEEKDAYS["en" if lang == "en" else "ru"][first.weekday()]}

    async def h_zone(params: Dict[str, Any], lang: str = "ru", **_k: Any) -> Dict[str, Any]:
        key = zone_key(params.get("city") or "") or (str(params.get("city") or "") if str(params.get("city") or "") in ZONES else "")
        if not key:
            raise ActionError(tr(lang, "Не знаю этот город. Есть столицы и крупные города мира, например Токио, Лондон, Нью-Йорк.",
                                 "Unknown city. Capitals and big cities work, e.g. Tokyo, London, New York."), "bad_params")
        now_utc = datetime.fromtimestamp(eng.clock(), tz=timezone.utc).replace(tzinfo=None)
        src_raw = str(params.get("from") or "")
        src = zone_key(src_raw) or (src_raw if src_raw in ZONES else "")
        clock = parse_clock(str(params.get("time") or "")) if params.get("time") else None
        if params.get("time") and not clock:
            raise ActionError(tr(lang, "Не понял время. Пример: 15:00 или 3pm.", "Unknown time. Example: 15:00 or 3pm."), "bad_params")
        if clock:
            if src:
                src_off = utc_offset(src, now_utc)
                src_label = zone_name(src, lang)
            else:
                local = datetime.now().astimezone()
                src_off = int((local.utcoffset() or timedelta()).total_seconds() // 60)
                src_label = tr(lang, "здесь", "here")
            local_day = (now_utc + timedelta(minutes=src_off)).date()
            at_utc = datetime.combine(local_day, datetime.min.time()) + timedelta(hours=clock[0], minutes=clock[1] - src_off)
            dst_off = utc_offset(key, at_utc)
            there = at_utc + timedelta(minutes=dst_off)
            shift = (there.date() - local_day).days
            day = "" if not shift else tr(lang, " (следующий день)" if shift > 0 else " (предыдущий день)", " (next day)" if shift > 0 else " (previous day)")
            msg = f"{clock[0]:02d}:{clock[1]:02d} {src_label} = {there:%H:%M} {zone_name(key, lang)}{day}"
            return {"message": msg, "copy": f"{there:%H:%M}", "speak": msg,
                    "details": [[zone_name(key, lang), fmt_offset(dst_off)]] + ([[src_label, fmt_offset(src_off)]] if src else [])}
        off = utc_offset(key, now_utc)
        there = now_utc + timedelta(minutes=off)
        local = datetime.now().astimezone()
        diff = off - int((local.utcoffset() or timedelta()).total_seconds() // 60)
        h, mi = divmod(abs(diff), 60)
        rel = tr(lang, "как у вас", "same as here") if not diff else \
            (("+" if diff > 0 else "−") + f"{h}" + (f":{mi:02d}" if mi else "") + tr(lang, " ч к вашему", " h vs. here"))
        msg = tr(lang, f"В городе {zone_name(key, lang)} сейчас {there:%H:%M}", f"It is {there:%H:%M} in {zone_name(key, lang)}")
        return {"message": msg, "copy": f"{there:%H:%M}", "speak": msg,
                "details": [[tr(lang, "Дата", "Date"), fmt_date(there.date(), lang)], [tr(lang, "Пояс", "Zone"), fmt_offset(off)],
                            [tr(lang, "Разница", "Difference"), rel]]}

    async def h_random(params: Dict[str, Any], lang: str = "ru", **_k: Any) -> Dict[str, Any]:
        kind = params.get("kind") or "number"
        rng = secrets.SystemRandom()
        if kind == "coin":
            side = rng.choice((tr(lang, "Орёл", "Heads"), tr(lang, "Решка", "Tails")))
            return {"message": side, "speak": side}
        if kind == "dice":
            count, sides = int(params.get("count") or 1), int(params.get("sides") or 6)
            rolls = [rng.randint(1, sides) for _ in range(count)]
            msg = " + ".join(map(str, rolls)) + (f" = {sum(rolls)}" if count > 1 else "")
            return {"message": msg, "copy": str(sum(rolls)), "speak": str(sum(rolls)), "details": [[tr(lang, "Бросок", "Roll"), f"{count}d{sides}"]]}
        if kind == "choice":
            items = [x.strip() for x in str(params.get("items") or "").split(",") if x.strip()]
            if len(items) < 2:
                raise ActionError(tr(lang, "Дайте хотя бы два варианта через запятую.", "Give at least two options, comma-separated."), "bad_params")
            pick = rng.choice(items)
            return {"message": pick, "copy": pick, "speak": pick, "details": [[tr(lang, "Из", "Out of"), str(len(items))]]}
        lo, hi = int(params.get("min") if params.get("min") is not None else 1), int(params.get("max") if params.get("max") is not None else 100)
        lo, hi = min(lo, hi), max(lo, hi)
        value = rng.randint(lo, hi)
        return {"message": str(value), "copy": str(value), "speak": str(value), "details": [[tr(lang, "Диапазон", "Range"), f"{lo} … {hi}"]]}

    async def h_lorem(params: Dict[str, Any], lang: str = "ru", **_k: Any) -> Dict[str, Any]:
        out = lorem(int(params.get("words") or 0), int(params.get("paragraphs") or 0))
        ok = await clip_set(out)
        return {"message": out, "kind": "text", "copy": out, "details": copied_note(ok, lang)}

    eng.handlers.update({"color.convert": h_color, "text.encode": h_encode, "json.format": h_json, "hash.text": h_hash,
                         "text.case": h_case, "text.stats": h_stats, "date.calc": h_date, "time.zone": h_zone,
                         "random.pick": h_random, "lorem.generate": h_lorem})
