"""Round 24 (r24-quick-settings): новые команды быстрого доступа + статус голоса.

Подключение — две строки в ``assistant_commands`` (``install()`` при импорте, ``bind(engine)`` в ``Engine``):
действия добавляются в ``assistant_catalog.ACTIONS/BY_ID``, фразы — В НАЧАЛО ``assistant_intents._RULES``,
обработчики — в ``engine.handlers``. Все действия кроссплатформенные (кроме копирования в буфер, которое на
не-Windows просто пропускается).

Действия:
  password.generate  — пароль (длина, символы), копируется в буфер; в журнал не пишется
  uuid.generate      — UUID v4
  unit.convert       — длина/масса/объём/скорость/данные/температура/время
  text.translate     — перевод текста или буфера моделью быстрого доступа
  text.summarize     — краткий пересказ текста/буфера/ссылки на страницу
  youtube.analyze    — анализ видео YouTube (кратко/подробно/таймкоды/вопрос), см. ``youtube_analysis``
  dfy.start          — «Сделай за меня»: карточка-передача в DFY (``/api/dfy/tasks``) — запускает страница
                       через ``window.MemuDFY`` по контракту; уровень ``confirm`` (агент действует в браузере)
"""
from __future__ import annotations

import re
import secrets
import string
import uuid
from typing import Any, Dict, List, Tuple

from . import assistant_catalog as catalog

A, _s, _n, _b, _e = catalog.A, catalog._s, catalog._n, catalog._b, catalog._e

UNITS: Dict[str, Tuple[str, float]] = {

    "mm": ("len", 0.001), "cm": ("len", 0.01), "m": ("len", 1.0), "km": ("len", 1000.0),
    "in": ("len", 0.0254), "ft": ("len", 0.3048), "yd": ("len", 0.9144), "mi": ("len", 1609.344), "nmi": ("len", 1852.0),
    "mg": ("mass", 1e-6), "g": ("mass", 0.001), "kg": ("mass", 1.0), "t": ("mass", 1000.0), "lb": ("mass", 0.45359237),
    "oz": ("mass", 0.028349523125),
    "ml": ("vol", 0.001), "l": ("vol", 1.0), "m3": ("vol", 1000.0), "gal": ("vol", 3.785411784), "floz": ("vol", 0.0295735295625),
    "kmh": ("speed", 1 / 3.6), "ms": ("speed", 1.0), "mph": ("speed", 0.44704), "kn": ("speed", 0.514444),
    "b": ("data", 1.0), "kb": ("data", 1024.0), "mb": ("data", 1024.0 ** 2), "gb": ("data", 1024.0 ** 3), "tb": ("data", 1024.0 ** 4),
    "s": ("time", 1.0), "min": ("time", 60.0), "h": ("time", 3600.0), "day": ("time", 86400.0), "week": ("time", 604800.0),
    "c": ("temp", 0), "f": ("temp", 0), "k": ("temp", 0),
}
ALIASES = {
    "мм": "mm", "миллиметр": "mm", "см": "cm", "сантиметр": "cm", "м": "m", "метр": "m", "км": "km", "километр": "km",
    "дюйм": "in", "inch": "in", "\"": "in", "фут": "ft", "foot": "ft", "feet": "ft", "ярд": "yd", "yard": "yd",
    "миля": "mi", "мил": "mi", "mile": "mi", "морская миля": "nmi",
    "мг": "mg", "г": "g", "гр": "g", "грамм": "g", "кг": "kg", "килограмм": "kg", "т": "t", "тонна": "t", "ton": "t",
    "фунт": "lb", "lbs": "lb", "pound": "lb", "унция": "oz", "ounce": "oz",
    "мл": "ml", "л": "l", "литр": "l", "liter": "l", "litre": "l", "галлон": "gal", "gallon": "gal",
    "км/ч": "kmh", "km/h": "kmh", "kph": "kmh", "м/с": "ms", "m/s": "ms", "миль/ч": "mph", "узел": "kn", "узл": "kn", "knot": "kn",
    "байт": "b", "byte": "b", "кб": "kb", "мб": "mb", "гб": "gb", "тб": "tb",
    "сек": "s", "секунд": "s", "sec": "s", "second": "s", "мин": "min", "минут": "min", "minute": "min",
    "час": "h", "hour": "h", "ч": "h", "день": "day", "дн": "day", "сут": "day", "неделя": "week", "недел": "week",
    "°c": "c", "℃": "c", "цельси": "c", "celsius": "c", "градус": "c", "°f": "f", "фаренгейт": "f", "fahrenheit": "f", "кельвин": "k", "kelvin": "k",

    "миллиграмм": "mg", "миллилитр": "ml", "тонн": "t", "унци": "oz", "дней": "day", "дня": "day",
    "килобайт": "kb", "мегабайт": "mb", "гигабайт": "gb", "терабайт": "tb",
    "kilometer": "km", "kilometre": "km", "meter": "m", "metre": "m", "centimeter": "cm", "centimetre": "cm",
    "millimeter": "mm", "millimetre": "mm", "kilogram": "kg", "gram": "g", "milligram": "mg", "milliliter": "ml",
    "millilitre": "ml", "kilobyte": "kb", "megabyte": "mb", "gigabyte": "gb", "terabyte": "tb",
    "days": "day", "weeks": "week", "hrs": "h", "mins": "min", "secs": "s",
}

def computer_supported() -> bool:
    """Round 31: Computer Use drives the real mouse/keyboard — Windows only. A function (not an inline check) so a
    test stand can run the whole Quick Access → model loop on Linux with a mocked OS layer."""
    import sys
    return sys.platform == "win32"

def unit_key(text: str) -> str:
    t = str(text or "").strip().lower().replace("ё", "е").rstrip(".")
    if t in UNITS:
        return t
    if t in ALIASES:
        return ALIASES[t]
    for k in sorted(ALIASES, key=len, reverse=True):
        if len(k) >= 3 and t.startswith(k):
            return ALIASES[k]
    for k in sorted(ALIASES, key=len, reverse=True):
        if len(k) >= 5 and k in t:
            return ALIASES[k]
    return ""

def convert(amount: float, src: str, dst: str) -> float:
    a, b = unit_key(src), unit_key(dst)
    if not a or not b:
        raise ValueError(f"неизвестная единица: {src if not a else dst}")
    qa, qb = UNITS[a][0], UNITS[b][0]
    if qa != qb:
        raise ValueError("единицы разных величин")
    if qa == "temp":
        c = amount if a == "c" else (amount - 32) * 5 / 9 if a == "f" else amount - 273.15
        return c if b == "c" else c * 9 / 5 + 32 if b == "f" else c + 273.15
    return amount * UNITS[a][1] / UNITS[b][1]

def fmt_num(x: float) -> str:
    if abs(x) >= 1e6 or (abs(x) < 1e-3 and x):
        return f"{x:.4g}"
    s = f"{x:,.4f}".rstrip("0").rstrip(".")
    return s.replace(",", "\u202f")

def make_password(length: int = 20, symbols: bool = True) -> str:
    length = max(8, min(128, int(length or 20)))
    pools = [string.ascii_lowercase, string.ascii_uppercase, string.digits] + (["!@#$%^&*()-_=+[]{};:,.?"] if symbols else [])
    alphabet = "".join(pools)
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(length))
        if all(any(c in pool for c in pw) for pool in pools):
            return pw

NEW_ACTIONS: List[Dict[str, Any]] = [
    A("password.generate", "assistant", "safe", "Сгенерировать пароль", "Generate a password",
      "Generate a strong random password locally and copy it to the clipboard (never logged).",
      {"length": _n("Length 8–128, default 20", 8, 128, integer=True), "symbols": _b("Include symbols (default true)")},
      windows=False),
    A("uuid.generate", "assistant", "safe", "Сгенерировать UUID", "Generate a UUID", "Random UUID v4, copied to the clipboard.",
      windows=False),
    A("unit.convert", "assistant", "safe", "Перевести единицы", "Convert units",
      "Convert length, mass, volume, speed, data size, time or temperature (km↔mi, kg↔lb, °C↔°F, GB↔MB…).",
      {"amount": _n("Amount"), "from": _s("Source unit"), "to": _s("Target unit")}, ("amount", "from", "to"), windows=False),
    A("text.translate", "assistant", "safe", "Перевести текст", "Translate text",
      "Translate the given text (or the clipboard when empty) with the Quick Ask model.",
      {"text": _s("Text; empty = clipboard"), "to": _s("Target language, e.g. \"en\", \"русский\"")}, windows=False, slow=True),
    A("text.summarize", "assistant", "safe", "Кратко пересказать", "Summarize",
      "Summarize the given text, a web page URL, or the browser tab the user opened Quick Ask from (page=true). "
      "With no text and no open tab it reads the clipboard.",
      {"text": _s("Text or URL; empty = the open tab, else the clipboard"),
       "page": _b("Summarize the browser tab the user came from")}, windows=False, slow=True),
    A("youtube.analyze", "web", "safe", "Анализ видео YouTube", "Analyze a YouTube video",
      "Fetch a YouTube video's captions (no API key) and summarize it: brief, detailed, a timestamped table of "
      "contents, or answer a question about it. Timestamps become clickable links.",
      {"url": _s("YouTube link; empty = clipboard"), "mode": _e(("brief", "detailed", "timestamps", "question")),
       "question": _s("Question about the video (mode=question)")}, windows=False, slow=True),
    A("dfy.start", "assistant", "confirm", "Сделай за меня (DFY)", "Do it for me (DFY)",
      "Hand a multi-step web task (book, fill a form, find and compare, sign up) to the Do-For-You agent with its "
      "own browser. The agent asks the user for logins/cards itself; never pass secrets here.",
      {"goal": _s("What to do, with all details the user gave")}, ("goal",), windows=False),
    A("computer.use", "assistant", "confirm", "Управление компьютером", "Computer use",
      "Hand mouse and keyboard to the Computer Use agent (vision model from Settings → Work) for a task in desktop "
      "apps. The user confirms the handover; every step asks for approval; Escape stops it. Windows only.",
      {"task": _s("What to do on the computer")}, ("task",)),
]
NEW_IDS = [a["id"] for a in NEW_ACTIONS]

TOOL_SYSTEM = ("You are a text tool inside MemuAI Quick Ask. Follow the instruction exactly and output only the "
               "result, without preambles or comments about the task.")

NO_AUDIT_PARAMS = {"password.generate", "text.translate", "text.summarize"}

def install() -> None:
    """Идемпотентно добавить действия в каталог и фразы в распознаватель."""
    for row in NEW_ACTIONS:
        if row["id"] not in catalog.BY_ID:
            catalog.ACTIONS.append(row)
            catalog.BY_ID[row["id"]] = row
    try:
        from . import assistant_intents as intents
    except Exception:
        return
    if any(name.startswith("r24.") for name, _p, _f in intents._RULES):
        return
    rules = [
        ("r24.youtube", re.compile(r"^(?:(?:проанализируй|разбери|перескажи|кратко|подробн\w*|конспект|таймкоды|оглавление|о ч[её]м|суммаризируй|summari[sz]e|analy[sz]e|recap|timestamps)\b[^\n]*?\s)?"
                                   r"(https?://(?:www\.|m\.)?(?:youtube\.com|youtu\.be)/\S+)(.*)$", re.I), _youtube),
        ("r24.youtube_clip", re.compile(r"^(?:проанализируй|разбери|перескажи|конспект|таймкоды)\s+(?:это\s+)?(?:видео|ролик)(?:\s+(?:с ютуба|из буфера|youtube))?$|^(?:summari[sz]e|analy[sz]e)\s+(?:this\s+)?(?:youtube\s+)?video$", re.I), _youtube_clip),
        ("r24.password", re.compile(r"^(?:сгенерируй|создай|придумай|generate|make)?\s*(?:надёжный|надежный|сложный|strong|random|случайный)?\s*(?:пароль|password)(?:\s+(?:на|из|длиной|of|with)?\s*(\d{1,3})\s*(?:символ\w*|знак\w*|chars?|characters?)?)?(\s+без\s+(?:спецсимволов|символов)|\s+without\s+symbols)?$", re.I), _password),
        ("r24.uuid", re.compile(r"^(?:сгенерируй|создай|generate|new)?\s*(?:uuid|guid|гуид)$", re.I), lambda m, ctx: ("uuid.generate", {})),
        ("r24.convert", re.compile(r"^(?:сколько\s+(?:будет\s+)?|переведи\s+|конвертируй\s+|convert\s+|how\s+many\s+\S+\s+(?:is|are|in)\s+)?(-?\d+(?:[.,]\d+)?)\s*([a-zа-яё°℃/\"]+(?:\s+[a-zа-яё]+)?)\s+(?:в|во|to|in|into|=)\s+([a-zа-яё°℃/]+(?:\s+[a-zа-яё]+)?)\??$", re.I), _convert),
        ("r24.translate", re.compile(r"^(?:переведи|translate)\s+(?:(?:выделенное|буфер|скопированное|это|clipboard|this)\s*)?(?:на|to|into)\s+(" + _LANGS + r")\b(?:\s+(?:язык\w*|language))?(?:\s*[:—-]\s*|\s+)?(.*)$", re.I), _translate),
        ("r24.translate_tail", re.compile(r"^(?:переведи|translate)\s+(.+?)\s+(?:на|to|into)\s+(" + _LANGS + r")\b(?:\s+(?:язык\w*|language))?[\s.!]*$", re.I),
         lambda m, ctx: _translate_to(m.group(2), m.group(1))),
        ("r24.summarize", re.compile(r"^(?:кратко\s+перескажи|перескажи\s+кратко|суммаризируй|summari[sz]e|"
                                     r"(?:сделай\s+|дай\s+|give\s+me\s+(?:a\s+)?)?(?:саммари|summary|tl;?dr)(?:\s+(?:of|для|по))?)"
                                     r"\s*(?:буфер\w*(?:\s+обмена)?|скопированное|(?:the\s+)?clipboard|это|this)?\b\s*(.*?)[\s?.!]*$", re.I), _summarize),
        ("r24.computer", re.compile(r"^(?:возьми\s+(?:управление|мышь)(?:\s+(?:над\s+)?(?:компьютером|компом|пк|мышью|мышкой))?|управляй\s+(?:компьютером|компом|пк)|computer\s+use|take\s+control(?:\s+of\s+(?:the\s+|my\s+)?(?:computer|pc|mouse))?)\s*[:,—-]?\s*(?:и\s+|and\s+)?(.+)$", re.I),
         lambda m, ctx: ("computer.use", {"task": m.group(1).strip()}) if len(m.group(1).strip()) >= 4 else None),
        ("r24.dfy", re.compile(r"^(?:сделай\s+за\s+меня|do\s+(?:it\s+)?for\s+me|dfy)\s*[:,—-]?\s*(.+)$", re.I), _dfy),
    ]
    intents._RULES[0:0] = rules

_LANGS = (r"(?:английск|русск|немецк|французск|испанск|итальянск|китайск|японск|корейск|украинск|белорусск|казахск|"
          r"турецк|арабск|португальск|польск|чешск|греческ|иврит|хинди|вьетнамск|тайск|индонезийск|нидерландск|голландск|"
          r"шведск|финск|норвежск|датск|венгерск|румынск|болгарск|сербск|грузинск|армянск|азербайджанск|узбекск|латыш|литовск|"
          r"эстонск|татарск|латин)\w*|english|russian|german|french|spanish|italian|chinese|japanese|korean|ukrainian|"
          r"kazakh|turkish|arabic|portuguese|polish|czech|greek|hebrew|hindi|vietnamese|thai|indonesian|dutch|swedish|"
          r"finnish|norwegian|danish|hungarian|romanian|bulgarian|serbian|georgian|armenian|latin|en|ru|de|fr|es|it|zh|ja|ko|uk")

def _youtube(m: "re.Match[str]", ctx: Any):
    url = m.group(1)
    rest = (m.group(2) or "").strip(" ?.!,;:—-")
    low = str(getattr(ctx, "text", "") or m.group(0)).lower()
    mode = "brief"
    if re.search(r"таймкод|оглавлен|timestamps|chapters", low):
        mode = "timestamps"
    elif re.search(r"подробн|конспект|detailed|in detail", low):
        mode = "detailed"
    if mode == "brief" and rest and len(rest.split()) >= 2 and not re.fullmatch(r"(?:кратко|briefly|подробно|in detail)", rest, re.I):
        return "youtube.analyze", {"url": url, "mode": "question", "question": rest}
    return "youtube.analyze", {"url": url, "mode": mode}

def _youtube_clip(m: "re.Match[str]", ctx: Any):
    mode = "timestamps" if "таймкод" in m.group(0).lower() else "brief"
    return "youtube.analyze", {"url": "", "mode": mode}

def _password(m: "re.Match[str]", ctx: Any):
    params: Dict[str, Any] = {}
    if m.group(1):
        params["length"] = max(8, min(128, int(m.group(1))))
    if m.group(2):
        params["symbols"] = False
    return "password.generate", params

def _convert(m: "re.Match[str]", ctx: Any):
    amount = float(m.group(1).replace(",", "."))
    a, b = unit_key(m.group(2)), unit_key(m.group(3))
    if not a or not b or UNITS[a][0] != UNITS[b][0]:
        return None
    return "unit.convert", {"amount": amount, "from": a, "to": b}

def _translate(m: "re.Match[str]", ctx: Any):
    text = (m.group(2) or "").strip()
    if text and str(getattr(ctx, "text", "")).endswith("?"):
        text += "?"
    return _translate_to(m.group(1), text)

def _translate_to(target: str, text: str):
    text = text.strip()
    if re.fullmatch(r"(?:выделенное|буфер\w*(?:\s+обмена)?|скопированное|это|(?:этот\s+)?текст|clipboard|this|it|"
                    r"(?:the\s+|this\s+)?text)", text, re.I):
        text = ""
    return "text.translate", {"to": target, "text": text}

_PAGE_REF = re.compile(r"^(?:(?:эт(?:у|ой|а|от|ого)|текущ\w+|открыт\w+|the|this|current)\s+)?"
                       r"(?:страниц\w*|стать\w*|вкладк\w*|сайт\w*|page|article|tab|site)$", re.I)

def _summarize(m: "re.Match[str]", ctx: Any):
    """«кратко перескажи» → буфер, «… https://…» → ссылка, «… эту страницу» → модели (окно читает вкладку).

    Короткая тема («кратко перескажи что такое квантовая физика») — НЕ текст для пересказа: пересказать
    буквально строку «что такое квантовая физика» бессмысленно, такую просьбу отвечает модель."""
    rest = (m.group(1) or "").strip()
    if not rest:
        return "text.summarize", {"text": ""}
    if _PAGE_REF.match(rest):

        return None
    if re.fullmatch(r"https?://\S+", rest):
        return "text.summarize", {"text": rest}
    return None

def _dfy(m: "re.Match[str]", ctx: Any):
    goal = m.group(1).strip()
    return ("dfy.start", {"goal": goal}) if len(goal) >= 4 else None

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

    async def h_password(params: Dict[str, Any], lang: str = "ru", **_k: Any) -> Dict[str, Any]:
        pw = make_password(params.get("length") or 20, params.get("symbols", True) is not False)
        copied = await clip_set(pw)
        return {"message": tr(lang, "Пароль готов" + (" и скопирован в буфер." if copied else "."),
                              "Password ready" + (" and copied." if copied else ".")),
                "kind": "secret", "details": [[tr(lang, "Пароль", "Password"), pw]], "copy": pw}

    async def h_uuid(params: Dict[str, Any], lang: str = "ru", **_k: Any) -> Dict[str, Any]:
        value = str(uuid.uuid4())
        copied = await clip_set(value)
        return {"message": value + (tr(lang, " — скопирован", " — copied") if copied else ""), "copy": value}

    async def h_convert(params: Dict[str, Any], lang: str = "ru", **_k: Any) -> Dict[str, Any]:
        try:
            out = convert(float(params["amount"]), str(params["from"]), str(params["to"]))
        except ValueError as exc:
            raise ActionError(str(exc), "bad_params")
        a, b = unit_key(params["from"]), unit_key(params["to"])
        msg = f"{fmt_num(float(params['amount']))} {a} = {fmt_num(out)} {b}"
        return {"message": msg, "copy": fmt_num(out), "speak": msg}

    async def text_or_clip(params: Dict[str, Any], lang: str) -> str:
        text = str(params.get("text") or "").strip() or (await clip_get()).strip()
        if not text:
            raise ActionError(tr(lang, "Нечего обрабатывать: буфер обмена пуст. Скопируйте текст или напишите его после команды.",
                                 "Nothing to process: the clipboard is empty. Copy text or type it after the command."), "empty")
        return text[:40_000]

    async def h_translate(params: Dict[str, Any], lang: str = "ru", **_k: Any) -> Dict[str, Any]:
        text = await text_or_clip(params, lang)
        target = str(params.get("to") or ("английский" if lang != "en" else "Russian"))
        out = await eng._ask_ai(f"Translate the text below into {target}. Return only the translation, keep formatting.\n\n<text>\n{text}\n</text>", lang,
                                system=TOOL_SYSTEM)
        copied = await clip_set(out)
        return {"message": out, "kind": "text", "copy": out,
                "details": [[tr(lang, "Буфер", "Clipboard"), tr(lang, "перевод скопирован", "copied")]] if copied else []}

    async def page_text(page: Dict[str, Any], lang: str) -> str:
        url = str(page.get("url") or "")
        text = ""
        if re.match(r"https?://", url):
            try:
                from . import websearch
                fetched = await websearch.fetch_page(url, limit=40_000)
                text = str(fetched.get("text") or "")
            except Exception:
                text = ""
        if len(text.strip()) < 200:
            try:
                manager = eng.quick_manager()
                shown = await eng._blocking(manager.page_text, str(page.get("id") or ""), 60_000)
                text = str(shown or "") if len(str(shown or "").strip()) > len(text.strip()) else text
            except Exception:
                pass
        if not text.strip():
            raise ActionError(tr(lang, "Не удалось прочитать страницу. Выделите текст и скопируйте его или дайте ссылку.",
                                 "Could not read the page. Copy its text or give a link."), "page_unreadable")
        return text[:40_000]

    async def h_summarize(params: Dict[str, Any], lang: str = "ru", source: str = "local", **_k: Any) -> Dict[str, Any]:
        from . import youtube_analysis as yt
        text = str(params.get("text") or "").strip()

        page = await eng.open_page() if params.get("page") or (not text and source == "model") else None
        if params.get("page") and page is None:
            raise ActionError(tr(lang, "Не вижу открытой страницы: вызовите быстрый доступ из вкладки браузера или дайте ссылку.",
                                 "No open page: call Quick Ask from a browser tab or give a link."), "no_page")
        title = ""
        if page is not None:
            url = str(page.get("url") or "")
            if yt.video_id(url):
                return await h_youtube({"url": url, "mode": "brief"}, lang=lang)
            title = str(page.get("title") or "")
            text = await page_text(page, lang)
        else:
            text = await text_or_clip(params, lang)

            if re.fullmatch(r"https?://\S+", text) and yt.video_id(text):
                return await h_youtube({"url": text, "mode": "brief"}, lang=lang)
            if re.fullmatch(r"https?://\S+", text):
                url = text
                try:
                    from . import websearch
                    fetched = await websearch.fetch_page(url, limit=40_000)
                    text, title = str(fetched.get("text") or "")[:40_000], str(fetched.get("title") or "")
                except Exception as exc:
                    raise ActionError(tr(lang, f"Не удалось открыть ссылку: {exc}", f"Could not open the link: {exc}"), "source") from None
                if not text.strip():
                    raise ActionError(tr(lang, "На странице нет текста.", "The page has no text."), "source")
                page = {"url": url, "title": title}
        out = await eng._ask_ai(("Кратко перескажи текст: 3–6 пунктов и вывод. Язык ответа — русский." if lang != "en" else
                                 "Summarize briefly: 3–6 bullets and a takeaway.")
                                + (f"\n\nTitle: {title}" if title else "") + f"\n\n<text>\n{text}\n</text>", lang, system=TOOL_SYSTEM)
        result: Dict[str, Any] = {"message": out, "kind": "text", "copy": out}
        if page is not None and page.get("url"):
            result["links"] = [{"label": title or str(page["url"]), "url": str(page["url"])}]
        return result

    async def h_youtube(params: Dict[str, Any], lang: str = "ru", **_k: Any) -> Dict[str, Any]:
        from . import youtube_analysis as yt
        url = str(params.get("url") or "").strip()
        if not yt.video_id(url):
            clip = await clip_get()
            url = yt.find_url(clip) or (clip.strip() if yt.video_id(clip.strip()) else "")
        if not yt.video_id(url):
            page = None
            try:
                manager = eng.quick_manager()
                page = getattr(manager, "page", None) or getattr(manager, "last_page", None)
            except Exception:
                page = None
            if isinstance(page, dict) and yt.video_id(page.get("url")):
                url = page["url"]
        if not yt.video_id(url):
            raise ActionError(tr(lang, "Дайте ссылку на видео YouTube (в команде, в буфере обмена или откройте видео во вкладке).",
                                 "Give a YouTube link (in the command, the clipboard, or an open tab)."), "bad_url")

        async def complete(prompt: str) -> str:
            return await eng._ask_ai(prompt + ("\n\nОтвечай по-русски." if lang != "en" else "\n\nAnswer in English."), lang, system=TOOL_SYSTEM)
        try:
            res = await yt.analyze(url, complete, mode=str(params.get("mode") or "brief"), lang=lang,
                                   question=str(params.get("question") or ""))
        except yt.TranscriptUnavailable as exc:
            raise ActionError(exc.text(lang), exc.code)
        tinfo = res["transcript"]
        meta = tr(lang, f"субтитры: {tinfo['lang']}{' (авто)' if tinfo['auto'] else ''}, {tinfo['words']} слов, {tinfo['source']}",
                  f"captions: {tinfo['lang']}{' (auto)' if tinfo['auto'] else ''}, {tinfo['words']} words, {tinfo['source']}")
        return {"message": res["text"], "kind": "youtube", "details": [[tr(lang, "Источник", "Source"), meta]],
                "links": [{"label": res["title"] or res["url"], "url": res["url"]}],
                "images": [res["thumbnail"]] if res.get("thumbnail") else [], "copy": res["text"],
                "youtube": {k: res[k] for k in ("id", "title", "author", "url", "mode")}}

    async def h_dfy(params: Dict[str, Any], lang: str = "ru", **_k: Any) -> Dict[str, Any]:
        goal = str(params.get("goal") or "").strip()

        return {"message": tr(lang, "Передаю задачу агенту «Сделай за меня»…", "Handing the task to the Do-For-You agent…"),
                "kind": "dfy", "status": "running", "client": {"type": "dfy", "goal": goal, "source": "quick"}}

    async def h_computer(params: Dict[str, Any], lang: str = "ru", **_k: Any) -> Dict[str, Any]:
        from .assistant_actions_win import Unsupported
        if not computer_supported():
            raise Unsupported("Управление компьютером работает только в Windows-приложении MemuAI.",
                              "Computer use works only in the MemuAI Windows app.")
        from .pgdata import load_preferences
        from .quick_ask_core import computer_model
        prefs = load_preferences()
        if prefs.get("computer_use_enabled") is False:
            raise ActionError(tr(lang, "Управление компьютером выключено (Настройки → Работа).", "Computer use is off (Settings → Work)."), "disabled")

        model = computer_model(prefs)
        if not model:
            raise ActionError(tr(lang, "Нет модели для управления компьютером: выберите модель в окне быстрого доступа или в Настройки → Работа.",
                                 "No model for computer use: pick a model in the Quick Access window or in Settings → Work."), "no_model")

        return {"message": tr(lang, "Передаю управление агенту. Каждое действие спросит разрешения; Escape — остановить.",
                              "Handing control to the agent. Each step asks for approval; Escape stops it."),
                "kind": "computer", "status": "running",
                "client": {"type": "computer", "task": str(params.get("task") or "")[:6000], "model": model,

                           "model_source": "work" if str(prefs.get("computer_model") or "").strip() and model == str(prefs.get("computer_model")).strip() else "quick",
                           "max_steps": int(prefs.get("computer_max_steps") or 12)}}

    eng.handlers.update({"computer.use": h_computer, "password.generate": h_password, "uuid.generate": h_uuid, "unit.convert": h_convert,
                         "text.translate": h_translate, "text.summarize": h_summarize, "youtube.analyze": h_youtube,
                         "dfy.start": h_dfy})

STT_LABELS = {"": ("как в «Голос и Live»", "as in Voice & Live"), "local": ("локальная модель", "local model"), "api": ("API", "API")}
TTS_LABELS = {"windows": ("голоса Windows", "Windows voices"), "downloaded": ("скачанный голос", "downloaded voice"),
              "api": ("API", "API"), "local": ("локальный сервер", "local server")}

def voice_status(prefs: Dict[str, Any], quick: Dict[str, Any], stt_models: List[Dict[str, Any]],
                 tts_models: List[Dict[str, Any]], windows_voices: List[Dict[str, Any]], platform: str = "win32",
                 lang: str = "ru") -> Dict[str, Any]:
    """Что реально будет использовано для распознавания и озвучки в быстром доступе и готово ли оно.

    Источник: свой выбор быстрого доступа (``quick``) поверх настроек «Голос и Live» (``prefs``). «API» — это ВСЕГДА
    OpenAI-совместимый адрес и ключ из Настройки → Голос и Live (``voice_*_api_url/_key``), а НЕ модели MemuAI Proxy:
    у Proxy нет эндпоинтов /audio/speech и /audio/transcriptions."""
    en = lang == "en"
    t = (lambda ru, e: e if en else ru)
    quick = quick or {}

    def eff(qkey: str, pkey: str, default: str = "") -> Tuple[str, str]:
        q = str(quick.get(qkey) or "")
        return (q, "quick") if q else (str(prefs.get(pkey) or default), "voice")

    stt_provider, stt_src = eff("stt_provider", "voice_stt_provider", "local")
    stt_provider = stt_provider if stt_provider in ("local", "api") else "local"
    stt: Dict[str, Any] = {"provider": stt_provider, "source": stt_src}
    if stt_provider == "api":
        model, _ = eff("stt_api_model", "voice_stt_api_model", "gpt-4o-mini-transcribe")
        url = str(prefs.get("voice_stt_api_url") or "")
        stt.update(model=model, endpoint=_host(url), ready=bool(url),
                   label=t(f"API: {model} · {_host(url) or 'адрес не задан'} (из «Голос и Live»)", f"API: {model} · {_host(url) or 'no URL'} (from Voice & Live)"),
                   problem="" if url else t("Не задан адрес API распознавания в Настройки → Голос и Live.", "No STT API URL in Settings → Voice & Live."),
                   fix="" if url else "open_voice")
    else:
        model_id, _ = eff("stt_local_model", "voice_stt_local_model", "small")
        row = next((m for m in stt_models if m.get("id") == model_id), None)
        ready = bool(row and row.get("downloaded"))
        stt.update(model=model_id, model_name=(row or {}).get("name") or model_id, ready=ready,
                   label=t(f"Локально: {(row or {}).get('name') or model_id} (faster-whisper)", f"Local: {(row or {}).get('name') or model_id} (faster-whisper)"),
                   problem="" if ready else t("Модель распознавания не скачана.", "The recognition model is not downloaded."),
                   fix="" if ready else "download", fix_id=model_id, fix_kind="stt",
                   downloading=bool(row and row.get("download_status") == "running"), progress=(row or {}).get("progress", 0))

    tts_provider, tts_src = eff("tts_provider", "voice_tts_provider", "windows")
    tts_provider = tts_provider if tts_provider in TTS_LABELS else "windows"
    tts: Dict[str, Any] = {"provider": tts_provider, "source": tts_src}
    if tts_provider == "windows":
        voice, _ = eff("tts_voice", "voice_tts_voice", "")
        ok = platform == "win32" and bool(windows_voices)
        tts.update(model=voice, ready=ok, label=t("Windows: " + (voice or "автоматически по языку"), "Windows: " + (voice or "automatic by language")),
                   problem="" if ok else t("Голоса Windows доступны только в Windows-приложении.", "Windows voices exist only in the Windows app."),
                   fix="" if ok else "pick_other")
    elif tts_provider == "downloaded":
        model_id, _ = eff("tts_local_model", "voice_tts_local_model", "")
        downloaded = [m for m in tts_models if m.get("downloaded")]
        row = next((m for m in tts_models if m.get("id") == model_id), None)
        ready = bool(row and row.get("downloaded")) or (not model_id and bool(downloaded))
        name = (row or {}).get("name") or model_id or (downloaded[0].get("name") if downloaded else "")
        first = model_id or next((m.get("id") for m in tts_models), "")
        tts.update(model=model_id, ready=ready, label=t("Скачанный голос: " + (name or "не выбран"), "Downloaded voice: " + (name or "none")),
                   problem="" if ready else t("Нет скачанного голоса — скачайте его ниже.", "No downloaded voice — download one below."),
                   fix="" if ready else "download", fix_id=first, fix_kind="tts",
                   downloading=bool(row and row.get("download_status") == "running"), progress=(row or {}).get("progress", 0))
    else:

        from . import tts_providers
        local = tts_provider == "local"
        url = str(prefs.get("voice_tts_local_url" if local else "voice_tts_api_url") or "")
        kind = "custom" if local else tts_providers.provider_for_url(url)
        meta = tts_providers.PROVIDERS[kind]
        model = str(prefs.get("voice_tts_local_model" if local else "voice_tts_api_model") or meta["default_model"] or "tts-1")
        voice = str(quick.get("tts_api_voice") or prefs.get("voice_tts_local_voice" if local else "voice_tts_api_voice")
                    or meta["default_voice"])
        where = t("локальный сервер", "local server") if local else (meta["label"] if kind != "custom" else "API")
        no_key = not local and kind != "custom" and not str(prefs.get("voice_tts_api_key") or "").strip()
        problem = (t("Не задан адрес озвучки в Настройки → Голос и Live.", "No TTS URL in Settings → Voice & Live.") if not url else
                   t(f"Не задан API-ключ {meta['label']} в Настройки → Голос и Live.", f"No {meta['label']} API key in Settings → Voice & Live.") if no_key else "")
        tts.update(model=model, voice=voice, endpoint=_host(url), service=kind, ready=not problem,
                   label=t(f"{where}: {model}, голос {voice} · {_host(url) or 'адрес не задан'} (из «Голос и Live»)",
                           f"{where}: {model}, voice {voice} · {_host(url) or 'no URL'} (from Voice & Live)"),
                   problem=problem, fix="open_voice" if problem else "")
    return {"stt": stt, "tts": tts, "proxy_note": t(
        "Модели MemuAI Proxy для голоса не используются: «API» — это адрес и ключ из Настройки → Голос и Live.",
        "MemuAI Proxy models are not used for voice: “API” means the URL and key from Settings → Voice & Live.")}

def _host(url: str) -> str:
    m = re.match(r"https?://([^/]+)", str(url or "").strip())
    return m.group(1) if m else ""

def register(app, eng: Any) -> None:
    """``GET /api/assistant/voice/status?lang=`` и ``/api/youtube/*``."""
    import asyncio
    import sys
    from pathlib import Path

    runtime_box: Dict[str, Any] = {}

    def runtime():
        if "rt" not in runtime_box:
            from .voice_runtime import VoiceRuntime
            from .config import DATA_DIR  # type: ignore
            runtime_box["rt"] = VoiceRuntime(DATA_DIR)
        return runtime_box["rt"]

    @app.get("/api/assistant/voice/status")
    async def assistant_voice_status(lang: str = "ru"):
        from .pgdata import load_preferences
        prefs = load_preferences()
        quick = (await asyncio.to_thread(eng.store.load))["settings"].get("voice") or {}
        try:
            rt = getattr(app.state, "voice_runtime", None) or runtime()
            root = str(prefs.get("voice_models_dir") or "").strip()
            if root:
                try: rt.set_root(root)
                except Exception: pass
            rt.set_custom_models(prefs.get("voice_custom_models") or [])
            stt_m = rt.catalog(prefs.get("voice_stt_local_model", "small"))
            tts_m = rt.tts_catalog(prefs.get("voice_tts_local_model", ""))
            wv = await asyncio.to_thread(rt.windows_voices)
        except Exception:
            stt_m, tts_m, wv = [], [], []
        return voice_status(prefs, quick, stt_m, tts_m, wv, sys.platform, "en" if lang == "en" else "ru")

    try:
        from . import youtube_analysis

        def factory():
            async def complete(prompt: str) -> str:
                return await eng._ask_ai(prompt, "ru", system=TOOL_SYSTEM)
            return complete
        youtube_analysis.register(app, factory)
    except Exception:
        pass
