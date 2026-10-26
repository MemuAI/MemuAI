"""Мгновенные ответы для веб-поиска: погода, время, курсы валют и криптовалют.

«Какая сегодня погода?» не нужен поисковик с тремя раундами модели — нужны данные.
Для нескольких частых вопросов, где ответ — число из надёжного открытого API,
поиск возвращает готовый ``answer`` рядом с обычной выдачей, и модели остаётся
только пересказать его. Источники бесплатные и без ключей:

* погода и местное время — Open-Meteo (геокодер и прогноз); место берётся из
  запроса («погода в Казани»), а без места — по IP-адресу (ipwho.is), и ответ
  прямо говорит, что место приблизительное;
* курсы валют — open.er-api.com (ежедневные курсы);
* биткоин и эфир — CoinGecko.

Любой сбой — просто ``None``: обычный поиск работает дальше. Модуль не пишет
на диск и ничего не запоминает, кроме IP-местоположения в памяти на час.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from .providers import http

_TIMEOUT = 6.0
_CYRILLIC = re.compile(r"[а-яё]", re.I)

_WEATHER = re.compile(
    r"(?i)\bweather\b|\bforecast\b.{0,24}\b(?:today|tomorrow|week|in|for)\b|погод|прогноз.{0,14}погод|"
    r"будет ли.{0,18}(?:дожд|снег|гроз)|нужен ли зонт|сколько градусов|какая температура")
_TIME = re.compile(
    r"(?i)(?:what(?:'s| is) the (?:current |local )?time|what time is it|current time|local time|^\s*time)\s+(?:in|at)\s+(?P<en>.+)|"
    r"(?:сколько (?:сейчас )?времени|который (?:сейчас )?час|текущее время|время)\s+(?:сейчас\s+)?в\s+(?P<ru>.+)")

_EXPLAIN = re.compile(r"(?i)\b(?:что такое|как работает|как образу\w+|почему|объясни\w*|расскажи про|what is|how does|why does|explain|define)\b")
_CRYPTO = re.compile(r"(?i)биткоин\w*|bitcoin|\bbtc\b|эфир(?:иум\w*)?|ethereum|\beth\b")
_PRICE_WORDS = re.compile(r"(?i)курс|цена|стоим|сколько|price|worth|rate|cost|how much")

_CODES = {
    "usd": "USD", "доллар": "USD", "долл": "USD", "бакс": "USD", "dollar": "USD",
    "eur": "EUR", "евро": "EUR", "euro": "EUR",
    "rub": "RUB", "руб": "RUB", "рубл": "RUB", "ruble": "RUB", "rouble": "RUB",
    "cny": "CNY", "юан": "CNY", "yuan": "CNY", "rmb": "CNY",
    "gbp": "GBP", "фунт": "GBP", "pound": "GBP", "sterling": "GBP",
    "jpy": "JPY", "иен": "JPY", "йен": "JPY", "yen": "JPY",
    "uah": "UAH", "гривн": "UAH", "hryvnia": "UAH",
    "kzt": "KZT", "тенге": "KZT", "tenge": "KZT",
    "byn": "BYN", "белорусск": "BYN",
    "chf": "CHF", "франк": "CHF", "franc": "CHF",
    "try": "TRY", "лир": "TRY", "lira": "TRY",
    "pln": "PLN", "злот": "PLN", "zloty": "PLN",
    "cad": "CAD", "aud": "AUD", "inr": "INR", "рупи": "INR", "rupee": "INR", "brl": "BRL", "czk": "CZK",
    "kgs": "KGS", "сом": "KGS", "uzs": "UZS", "amd": "AMD", "драм": "AMD", "gel": "GEL", "лари": "GEL",
    "azn": "AZN", "aed": "AED", "дирхам": "AED", "sek": "SEK", "nok": "NOK", "dkk": "DKK", "krw": "KRW",
    "thb": "THB", "бат": "THB",
}

_STEM_ENDINGS = {"сом": r"(?:а|ов|ы|ах|ам|ом)?", "бат": r"(?:а|ов|ы|ах|ам)?", "лир": r"(?:а|ы|у|ой|ах|ам)?", "лари": "",
                 "драм": r"(?:а|ов|ы|ах|ам)?", "франк": r"(?:а|ов|и|ах|ам)?"}

def _money_pattern(key: str) -> str:
    if key in _STEM_ENDINGS:
        return re.escape(key) + _STEM_ENDINGS[key] + r"(?![a-zа-яё])"
    if re.fullmatch(r"[a-z]{3}", key):
        return re.escape(key) + r"(?![a-zа-яё])"
    return re.escape(key) + r"\w*"

_CODE_ALT = "|".join(_money_pattern(key) for key in sorted(_CODES, key=len, reverse=True))
_MONEY = re.compile(rf"(?i)(?<![a-zа-яё])(?:{_CODE_ALT})")
_CONVERT = re.compile(
    rf"(?i)(?P<amount>\d[\d\s]*(?:[.,]\d+)?)\s*(?P<src>(?<![a-zа-яё])(?:{_CODE_ALT}))\s*(?:в|to|in|=|->|→|into|на)\s*(?P<dst>(?:{_CODE_ALT}))")

def _is_ru(query: str, lang: str = "") -> bool:
    if _CYRILLIC.search(query):
        return True
    return not re.search(r"[a-z]", query, re.I) and lang == "ru"

def _code(word: str) -> str:
    low = word.lower()
    for key in sorted(_CODES, key=len, reverse=True):
        if low.startswith(key):
            return _CODES[key]
    return ""

def currency_code(name: str) -> str:
    """ISO code of a currency named by its code («usd», «KZT») or word («доллары», «euro»); ``""`` when unknown."""
    text = str(name or "").strip().lower().replace("ё", "е")
    if re.fullmatch(r"[a-z]{3}", text):
        return text.upper()
    return _code(text) if _MONEY.fullmatch(text) else ""

def intent(query: str) -> str:
    """``weather`` / ``time`` / ``currency`` / ``crypto`` / ``""`` — what kind of instant answer the query asks for."""
    text = " ".join(str(query or "").split())[:400]
    if not text:
        return ""
    if _WEATHER.search(text) and not (_EXPLAIN.search(text) and not place_from_query(text)):
        return "weather"
    if _CRYPTO.search(text) and _PRICE_WORDS.search(text):
        return "crypto"
    if _CONVERT.search(text) or (re.search(r"(?i)\bкурс\w*|\bexchange rate\b|\brate of\b|\bto (?:usd|eur|rub)\b", text)
                                 and _MONEY.search(text)):
        return "currency"
    if _TIME.search(text):
        return "time"
    return ""

_LEAD_WORDS = r"(?:сегодня|завтра|послезавтра|сейчас|вчера|на неделю|на выходные|на \d+ дн\w*|в понедельник|в ближайшие|today|tomorrow|now|tonight|this week|this weekend|right now|please|пожалуйста)"
_PLACE_AFTER = re.compile(
    rf"(?i)(?:\b(?:в|во|для|по|около|возле|near|in|at|for)\s+)(?P<place>[A-Za-zА-Яа-яЁё][\w.\-’' ]{{1,40}}?)"
    rf"(?=\s*(?:{_LEAD_WORDS}|[?!.,;:]|$))")
_NOT_A_PLACE = re.compile(r"(?i)^(?:мо(?:ём|ем|й|я|ей|и)\b|мне\b|на\b|этом|эт\w+|целом|общем|тот|какой|какая|какие|будет|ближайш\w+|\d+)")

def place_from_query(query: str) -> str:
    """The place the query names ("погода в Казани завтра" → "Казани"), or an empty string."""
    text = " ".join(str(query or "").split())
    best = ""
    for match in _PLACE_AFTER.finditer(text):
        place = match.group("place").strip(" -.,")
        place = re.sub(rf"(?i)\s+{_LEAD_WORDS}.*$", "", place).strip()
        if not place or _NOT_A_PLACE.match(place) or len(place) < 2:
            continue
        best = place
    return best

def _nominative_candidates(place: str) -> List[str]:
    """Names to try in the geocoder: Russian prepositional case ("в Казани") is not in its index."""
    place = place.strip()
    variants: List[str] = []

    def add(value: str) -> None:
        value = value.strip(" -")
        if value and value not in variants:
            variants.append(value)

    def inflect(word: str) -> List[str]:
        out = [word]
        if not _CYRILLIC.search(word) or len(word) < 4:
            return out
        if word.endswith(("е", "у", "ю")):
            out += [word[:-1], word[:-1] + "а", word[:-1] + "я"]
        if word.endswith("и"):
            out += [word[:-1] + "ь", word[:-1] + "я", word[:-1] + "а", word[:-1]]
        if word.endswith(("ом", "ем")):
            out += [word[:-2], word[:-2] + "а"]
        if word.endswith("ой"):
            out += [word[:-2] + "а", word[:-2] + "ая"]
        if word.endswith("ы"):
            out += [word[:-1], word[:-1] + "а"]
        return out

    if "-на-" in place.lower():
        head, tail = re.split(r"(?i)-на-", place, maxsplit=1)
        for option in inflect(head):
            add(f"{option}-на-{tail}")
    parts = place.split(" ")
    if len(parts) > 1:
        for option in inflect(parts[-1]):
            add(" ".join(parts[:-1] + [option]))
        for option in inflect(parts[0]):
            add(" ".join([option] + parts[1:]))
    for option in inflect(place):
        add(option)
    return variants[:7]

async def _get_json(url: str, params: Dict[str, Any], proxy: str = "") -> Dict[str, Any]:
    session = await http.get_session(proxy)
    resp = await session.get(url, params=params, headers={"Accept": "application/json"}, timeout=_TIMEOUT)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    data = resp.json()
    return data if isinstance(data, dict) else {}

async def geocode(place: str, lang: str = "ru", proxy: str = "") -> Optional[Dict[str, Any]]:
    """{"name", "country", "lat", "lon"} of the most populous place that fits, or None."""
    language = "ru" if _CYRILLIC.search(place) or lang == "ru" else "en"

    async def ask(name: str) -> List[Dict[str, Any]]:
        try:
            data = await _get_json("https://geocoding-api.open-meteo.com/v1/search",
                                   {"name": name, "count": 6, "language": language, "format": "json"}, proxy)
        except Exception:
            return []
        return [row for row in (data.get("results") or []) if isinstance(row, dict)]

    candidates = _nominative_candidates(place) if _CYRILLIC.search(place) else [place]
    batches = await asyncio.gather(*(ask(name) for name in candidates))
    for name, rows in zip(candidates, batches):
        rows = [row for row in rows if row.get("latitude") is not None and row.get("longitude") is not None]
        if not rows:
            continue
        prefix = name[:4].lower()
        fitting = [row for row in rows if str(row.get("name") or "").lower().startswith(prefix)] or rows
        row = max(fitting, key=lambda item: int(item.get("population") or 0))
        return {"name": str(row.get("name") or place), "country": str(row.get("country") or ""),
                "region": str(row.get("admin1") or ""), "lat": float(row["latitude"]), "lon": float(row["longitude"]),
                "tz": str(row.get("timezone") or "")}
    return None

_IP_PLACE: Dict[str, Tuple[float, Dict[str, Any]]] = {}

async def place_from_ip(proxy: str = "", lang: str = "en") -> Optional[Dict[str, Any]]:
    """Approximate place of this computer from its IP address (kept in memory for an hour)."""
    lang = "ru" if lang == "ru" else "en"
    stamp, cached = _IP_PLACE.get(lang, (0.0, {}))
    if cached and time.time() - stamp < 3600:
        return cached
    try:
        data = await _get_json("https://ipwho.is/", {"lang": lang}, proxy)
    except Exception:
        return None
    if not data.get("success", True) or data.get("latitude") is None or data.get("longitude") is None:
        return None
    place = {"name": str(data.get("city") or data.get("region") or data.get("country") or ""),
             "country": str(data.get("country") or ""), "region": str(data.get("region") or ""),
             "lat": float(data["latitude"]), "lon": float(data["longitude"]), "approximate": True}
    _IP_PLACE[lang] = (time.time(), place)
    return place

_WMO_RU = {0: "ясно", 1: "в основном ясно", 2: "переменная облачность", 3: "пасмурно", 45: "туман", 48: "изморозь",
           51: "лёгкая морось", 53: "морось", 55: "сильная морось", 56: "ледяная морось", 57: "ледяная морось",
           61: "небольшой дождь", 63: "дождь", 65: "сильный дождь", 66: "ледяной дождь", 67: "ледяной дождь",
           71: "небольшой снег", 73: "снег", 75: "сильный снег", 77: "снежные зёрна", 80: "небольшие ливни",
           81: "ливни", 82: "сильные ливни", 85: "снегопад", 86: "сильный снегопад", 95: "гроза",
           96: "гроза с градом", 99: "сильная гроза с градом"}
_WMO_EN = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "rime fog",
           51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
           61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
           71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
           82: "heavy showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorm",
           96: "thunderstorm with hail", 99: "severe thunderstorm with hail"}

def _temp(value: Any) -> str:
    try:
        number = round(float(value))
    except (TypeError, ValueError):
        return "?"
    return f"{number:+d} °C" if number else "0 °C"

def _day_name(index: int, ru: bool) -> str:
    return (("сегодня", "завтра", "послезавтра") if ru else ("today", "tomorrow", "the day after"))[index]

async def weather(query: str, lang: str = "", proxy: str = "") -> Optional[Dict[str, Any]]:
    ru = _is_ru(query, lang)
    named = place_from_query(query)
    place: Optional[Dict[str, Any]] = None
    approximate = False
    if named:
        place = await geocode(named, "ru" if ru else "en", proxy)
    if place is None:
        place = await place_from_ip(proxy, "ru" if ru else "en")
        approximate = place is not None
    if place is None:
        return None
    try:
        data = await _get_json("https://api.open-meteo.com/v1/forecast", {
            "latitude": place["lat"], "longitude": place["lon"], "timezone": "auto", "forecast_days": 3,
            "wind_speed_unit": "ms",
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum"},
            proxy)
    except Exception:
        return None
    current = data.get("current") or {}
    daily = data.get("daily") or {}
    if not current:
        return None
    names = _WMO_RU if ru else _WMO_EN
    where = place["name"] + (f", {place['country']}" if place.get("country") else "")
    local = str(current.get("time") or "").replace("T", " ")
    now = (f"Сейчас {_temp(current.get('temperature_2m'))} (ощущается как {_temp(current.get('apparent_temperature'))}), "
           f"{names.get(int(current.get('weather_code') or 0), '')}, ветер {current.get('wind_speed_10m')} м/с, "
           f"влажность {current.get('relative_humidity_2m')}%, осадки сейчас {current.get('precipitation')} мм."
           if ru else
           f"Now {_temp(current.get('temperature_2m'))} (feels like {_temp(current.get('apparent_temperature'))}), "
           f"{names.get(int(current.get('weather_code') or 0), '')}, wind {current.get('wind_speed_10m')} m/s, "
           f"humidity {current.get('relative_humidity_2m')}%, precipitation now {current.get('precipitation')} mm.")
    days: List[str] = []
    for index in range(min(3, len(daily.get("time") or []))):
        code = int((daily.get("weather_code") or [0] * 3)[index] or 0)
        low = (daily.get("temperature_2m_min") or [None] * 3)[index]
        high = (daily.get("temperature_2m_max") or [None] * 3)[index]
        chance = (daily.get("precipitation_probability_max") or [None] * 3)[index]
        amount = (daily.get("precipitation_sum") or [None] * 3)[index]
        rain = (f", вероятность осадков {chance}%" if ru else f", chance of precipitation {chance}%") if chance is not None else ""
        if amount:
            rain += f", {amount} мм" if ru else f", {amount} mm"
        days.append(f"{_day_name(index, ru).capitalize()}: {_temp(low)} … {_temp(high)}, {names.get(code, '')}{rain}.")
    note = ""
    if approximate:
        note = ("Место определено приблизительно по IP-адресу — назовите в ответе город, для которого даны цифры, "
                "и предложите уточнить другой." if ru else
                "The place was guessed from the IP address — say which city the numbers are for and offer to check another.")
    head = (f"Погода — {where}" if ru else f"Weather — {where}") + (" (по IP, приблизительно)" if approximate and ru else
                                                                     " (from IP, approximate)" if approximate else "")
    text = "\n".join([head, now] + days + [f"Местное время в точке: {local}." if ru else f"Local time there: {local}."])
    return {"kind": "weather", "title": head, "text": text, "note": note, "source": "Open-Meteo",
            "url": "https://open-meteo.com/", "place": where, "approximate": approximate}

async def local_time(query: str, lang: str = "", proxy: str = "") -> Optional[Dict[str, Any]]:
    match = _TIME.search(query)
    if not match:
        return None
    named = (match.group("en") or match.group("ru") or "").strip(" ?!.,")
    named = re.sub(rf"(?i)\s+{_LEAD_WORDS}.*$", "", named).strip()
    if not named:
        return None
    ru = _is_ru(query, lang)
    place = await geocode(named, "ru" if ru else "en", proxy)
    if place is None:
        return None
    try:
        data = await _get_json("https://api.open-meteo.com/v1/forecast", {
            "latitude": place["lat"], "longitude": place["lon"], "timezone": "auto", "current": "temperature_2m"}, proxy)
    except Exception:
        return None
    stamp = str((data.get("current") or {}).get("time") or "")
    if not stamp:
        return None
    where = place["name"] + (f", {place['country']}" if place.get("country") else "")
    zone = str(data.get("timezone_abbreviation") or data.get("timezone") or "")
    day, clock = stamp.split("T") if "T" in stamp else (stamp, "")
    text = (f"Местное время — {where}: {clock} ({day}), часовой пояс {zone}." if ru
            else f"Local time — {where}: {clock} ({day}), time zone {zone}.")
    return {"kind": "time", "title": where, "text": text, "note": "", "source": "Open-Meteo",
            "url": "https://open-meteo.com/", "place": where, "approximate": False}

async def currency(query: str, lang: str = "", proxy: str = "") -> Optional[Dict[str, Any]]:
    ru = _is_ru(query, lang)
    amount = 1.0
    src = dst = ""
    match = _CONVERT.search(query)
    if match:
        try:
            amount = float(re.sub(r"\s", "", match.group("amount")).replace(",", "."))
        except ValueError:
            amount = 1.0
        src, dst = _code(match.group("src")), _code(match.group("dst"))
    else:
        found: List[str] = []
        for word in _MONEY.findall(query):
            code = _code(word)
            if code and code not in found:
                found.append(code)
        if not found:
            return None
        src = found[0]
        dst = found[1] if len(found) > 1 else ("RUB" if ru and src != "RUB" else "USD" if src != "USD" else "EUR")
        number = re.search(r"(\d+(?:[.,]\d+)?)", query)
        if number and len(found) > 1:
            amount = float(number.group(1).replace(",", "."))
    if not src or not dst or src == dst:
        return None
    try:
        data = await _get_json(f"https://open.er-api.com/v6/latest/{src}", {}, proxy)
    except Exception:
        return None
    rate = (data.get("rates") or {}).get(dst)
    if not rate:
        return None
    total = amount * float(rate)
    shown = f"{total:,.2f}".replace(",", " ") if total >= 1 else f"{total:.4f}"
    updated = str(data.get("time_last_update_utc") or "")[:16]
    text = (f"Курс: 1 {src} = {float(rate):.4f} {dst}" + (f"; {amount:g} {src} = {shown} {dst}" if amount != 1 else "")
            + f". Курсы обновляются раз в сутки (данные на {updated} UTC)." if ru else
            f"Rate: 1 {src} = {float(rate):.4f} {dst}" + (f"; {amount:g} {src} = {shown} {dst}" if amount != 1 else "")
            + f". Rates update once a day (data as of {updated} UTC).")
    return {"kind": "currency", "title": f"{src}/{dst}", "text": text,
            "note": "Это среднерыночный курс, не курс обменника или банка." if ru else
                    "This is the mid-market rate, not a bank or exchange-office rate.",
            "source": "open.er-api.com", "url": "https://www.exchangerate-api.com/", "approximate": False}

async def crypto(query: str, lang: str = "", proxy: str = "") -> Optional[Dict[str, Any]]:
    low = query.lower()
    ids = []
    if re.search(r"биткоин|bitcoin|\bbtc\b", low):
        ids.append(("bitcoin", "BTC"))
    if re.search(r"эфир|ethereum|\beth\b", low):
        ids.append(("ethereum", "ETH"))
    if not ids:
        return None
    ru = _is_ru(query, lang)
    try:
        data = await _get_json("https://api.coingecko.com/api/v3/simple/price", {
            "ids": ",".join(name for name, _ in ids), "vs_currencies": "usd,eur,rub", "include_24hr_change": "true"},
            proxy)
    except Exception:
        return None
    rows: List[str] = []
    for name, symbol in ids:
        row = data.get(name) or {}
        if not row.get("usd"):
            continue
        change = row.get("usd_24h_change")
        move = f", за сутки {change:+.1f}%" if ru and change is not None else (
            f", 24h {change:+.1f}%" if change is not None else "")
        rows.append(f"{symbol}: ${row['usd']:,.0f} · {row.get('eur', 0):,.0f} € · {row.get('rub', 0):,.0f} ₽{move}".replace(",", " "))
    if not rows:
        return None
    return {"kind": "crypto", "title": "Crypto", "text": ("Цены (CoinGecko): " if ru else "Prices (CoinGecko): ") + "; ".join(rows),
            "note": "", "source": "CoinGecko", "url": "https://www.coingecko.com/", "approximate": False}

async def answer(query: str, *, lang: str = "", proxy: str = "") -> Optional[Dict[str, Any]]:
    """The instant answer for ``query``, or ``None`` (no such intent, or the source did not answer in time)."""
    kind = intent(query)
    if not kind:
        return None
    worker = {"weather": weather, "time": local_time, "currency": currency, "crypto": crypto}[kind]
    try:
        return await asyncio.wait_for(worker(query, lang, proxy), _TIMEOUT + 3)
    except Exception:
        return None
