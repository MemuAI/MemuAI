# -*- coding: utf-8 -*-
"""Text utilities: case, lines, replace, statistics, transliteration, typography, extraction, diff…"""
from __future__ import annotations

import difflib
import html
import random
import re
import unicodedata
from collections import Counter
from typing import Any, Dict, List

from .core import O, UtilError, lines_of, table, utility

# ── case ────────────────────────────────────────────────────────────────────────────────────────────────────────────
_WORD = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+")

def _words(text: str) -> List[str]:
    spaced = re.sub(r"([a-zа-яё0-9])([A-ZА-ЯЁ])", r"\1 \2", text)
    spaced = re.sub(r"([A-ZА-ЯЁ]+)([A-ZА-ЯЁ][a-zа-яё])", r"\1 \2", spaced)
    return _WORD.findall(spaced)

def _sentence(text: str) -> str:
    out, cap = [], True
    for ch in text.lower():
        if cap and ch.isalpha():
            out.append(ch.upper())
            cap = False
        else:
            out.append(ch)
        if ch in ".!?…\n":
            cap = True
    return "".join(out)

CASES = [("upper", "ВСЕ ПРОПИСНЫЕ", "UPPER CASE"), ("lower", "все строчные", "lower case"),
         ("title", "Каждое Слово С Заглавной", "Title Case"), ("sentence", "Как в предложении", "Sentence case"),
         ("swap", "иНВЕРСИЯ рЕГИСТРА", "sWAP cASE"), ("camel", "camelCase", "camelCase"), ("pascal", "PascalCase", "PascalCase"),
         ("snake", "snake_case", "snake_case"), ("kebab", "kebab-case", "kebab-case"), ("constant", "CONSTANT_CASE", "CONSTANT_CASE"),
         ("dot", "dot.case", "dot.case"), ("alternating", "чЕрЕдОвАнИе", "aLtErNaTiNg")]

@utility("text.case", "text", "Регистр и стиль имён", "Change case",
         "ВЕРХНИЙ, нижний, Заглавные, camelCase, snake_case, kebab-case и ещё 6 вариантов.",
         "UPPER, lower, Title, camelCase, snake_case, kebab-case and 6 more.",
         opts=[O("mode", "choice", "Вариант", "Mode", "upper", choices=CASES)], live=True, ai_default=True,
         keys="регистр заглавные прописные case upper lower camel snake kebab")
def text_case(ctx):
    text, mode = ctx.need_text(), ctx.o["mode"]
    if mode in ("upper", "lower", "swap"):
        return getattr(text, {"swap": "swapcase"}.get(mode, mode))()
    if mode == "title":
        return re.sub(r"[^\W_]+", lambda m: m.group(0)[:1].upper() + m.group(0)[1:].lower(), text)
    if mode == "sentence":
        return _sentence(text)
    if mode == "alternating":
        flip, out = False, []
        for ch in text:
            if ch.isalpha():
                out.append(ch.upper() if flip else ch.lower())
                flip = not flip
            else:
                out.append(ch)
        return "".join(out)
    rows = []
    for line in lines_of(text):
        words = [w.lower() for w in _words(line)]
        if not words:
            rows.append(line)
            continue
        if mode == "camel":
            rows.append(words[0] + "".join(w[:1].upper() + w[1:] for w in words[1:]))
        elif mode == "pascal":
            rows.append("".join(w[:1].upper() + w[1:] for w in words))
        else:
            sep = {"snake": "_", "constant": "_", "kebab": "-", "dot": "."}[mode]
            joined = sep.join(words)
            rows.append(joined.upper() if mode == "constant" else joined)
    return "\n".join(rows)

# ── lines ───────────────────────────────────────────────────────────────────────────────────────────────────────────
LINE_OPS = [("sort", "Сортировать А→Я", "Sort A→Z"), ("sort_desc", "Сортировать Я→А", "Sort Z→A"),
            ("natural", "Сортировать «по-человечески» (file2 < file10)", "Natural sort (file2 < file10)"),
            ("length", "Сортировать по длине", "Sort by length"), ("unique", "Убрать повторы", "Remove duplicates"),
            ("dups", "Только повторяющиеся", "Only duplicates"), ("count", "Посчитать повторы", "Count duplicates"),
            ("shuffle", "Перемешать", "Shuffle"), ("reverse", "Обратный порядок", "Reverse order"),
            ("number", "Пронумеровать", "Number lines"), ("empty", "Убрать пустые строки", "Remove empty lines"),
            ("trim", "Обрезать пробелы по краям", "Trim spaces"), ("prefix", "Добавить в начало", "Add prefix"),
            ("suffix", "Добавить в конец", "Add suffix"), ("wrapq", "Обернуть в кавычки + запятые", "Quote + commas"),
            ("grep", "Оставить строки, где есть…", "Keep lines containing…"),
            ("grepv", "Убрать строки, где есть…", "Remove lines containing…"),
            ("join", "Склеить в одну строку", "Join into one line"), ("split", "Разбить по разделителю", "Split by separator")]

def _natural_key(text: str):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]

@utility("text.lines", "text", "Строки: сортировка, повторы, нумерация", "Lines: sort, dedupe, number",
         "Сортировка, удаление дублей, перемешивание, нумерация, фильтр, склейка и разбиение строк.",
         "Sort, dedupe, shuffle, number, filter, join and split lines.",
         opts=[O("op", "choice", "Действие", "Action", "sort", choices=LINE_OPS),
               O("value", "text", "Текст / разделитель", "Text / separator", "", show="op=prefix|suffix|grep|grepv|join|split",
                 hint="Для «склеить» и «разбить»: \\n — перенос, \\t — табуляция", hint_en="For join/split: \\n = newline, \\t = tab"),
               O("icase", "bool", "Без учёта регистра", "Ignore case", True, show="op=sort|sort_desc|unique|dups|count|grep|grepv")],
         live=True, ai_default=True, keys="строки сортировка дубликаты уникальные lines sort unique dedupe shuffle")
def text_lines(ctx):
    text, op, value, icase = ctx.need_text(), ctx.o["op"], ctx.o["value"], ctx.o["icase"]
    rows = lines_of(text)
    if rows and rows[-1] == "" and op not in ("join", "split"):
        rows = rows[:-1]
    key = (lambda s: s.lower()) if icase else (lambda s: s)
    sep = str(value).replace("\\n", "\n").replace("\\t", "\t")
    if op == "sort":
        rows.sort(key=key)
    elif op == "sort_desc":
        rows.sort(key=key, reverse=True)
    elif op == "natural":
        rows.sort(key=_natural_key)
    elif op == "length":
        rows.sort(key=len)
    elif op == "unique":
        seen, out = set(), []
        for row in rows:
            if key(row) not in seen:
                seen.add(key(row))
                out.append(row)
        removed = len(rows) - len(out)
        return {"text": "\n".join(out), "note": ctx.tr(f"Убрано повторов: {removed}", f"Duplicates removed: {removed}")}
    elif op in ("dups", "count"):
        counts = Counter(key(r) for r in rows)
        first: Dict[str, str] = {}
        for r in rows:
            first.setdefault(key(r), r)
        if op == "dups":
            rows = [first[k] for k, n in counts.items() if n > 1]
        else:
            pairs = sorted(counts.items(), key=lambda kv: -kv[1])
            return {"text": "\n".join(f"{n}\t{first[k]}" for k, n in pairs),
                    "table": table([ctx.tr("Строка", "Line"), ctx.tr("Раз", "Times")], [[first[k], n] for k, n in pairs[:500]])}
    elif op == "shuffle":
        random.SystemRandom().shuffle(rows)
    elif op == "reverse":
        rows.reverse()
    elif op == "number":
        width = len(str(len(rows)))
        rows = [f"{i:>{width}}. {r}" for i, r in enumerate(rows, 1)]
    elif op == "empty":
        rows = [r for r in rows if r.strip()]
    elif op == "trim":
        rows = [r.strip() for r in rows]
    elif op == "prefix":
        rows = [sep + r for r in rows]
    elif op == "suffix":
        rows = [r + sep for r in rows]
    elif op == "wrapq":
        rows = [f'"{r}",' for r in rows if r.strip()]
        if rows:
            rows[-1] = rows[-1].rstrip(",")
    elif op in ("grep", "grepv"):
        needle = key(sep)
        rows = [r for r in rows if (needle in key(r)) == (op == "grep")]
    elif op == "join":
        return (sep if value != "" else ", ").join(r for r in rows if r.strip())
    elif op == "split":
        if not sep:
            raise UtilError("Укажите разделитель", "Enter a separator")
        return "\n".join(part.strip() for part in text.split(sep))
    return "\n".join(rows)

# ── replace ─────────────────────────────────────────────────────────────────────────────────────────────────────────
@utility("text.replace", "text", "Найти и заменить", "Find and replace",
         "Замена текста, в том числе регулярными выражениями, с подсчётом замен.",
         "Replace text, plain or with regular expressions, and count the replacements.",
         opts=[O("find", "text", "Найти", "Find", ""), O("repl", "text", "Заменить на", "Replace with", "",
                 hint="В режиме регулярок доступны \\1 и \\g<name>", hint_en="Regex mode supports \\1 and \\g<name>"),
               O("regex", "bool", "Регулярное выражение", "Regular expression", False),
               O("icase", "bool", "Без учёта регистра", "Ignore case", False),
               O("word", "bool", "Только целые слова", "Whole words only", False)],
         live=True, ai_default=True, keys="замена найти заменить regex replace find")
def text_replace(ctx):
    text, find = ctx.need_text(), ctx.o["find"]
    if not find:
        raise UtilError("Укажите, что искать", "Enter what to find")
    pattern = find if ctx.o["regex"] else re.escape(find)
    if ctx.o["word"]:
        pattern = rf"(?<!\w)(?:{pattern})(?!\w)"
    try:
        rx = re.compile(pattern, re.IGNORECASE if ctx.o["icase"] else 0)
    except re.error as exc:
        raise UtilError(f"Ошибка в регулярном выражении: {exc}", f"Regular expression error: {exc}")
    repl = ctx.o["repl"].replace("\\n", "\n").replace("\\t", "\t")
    try:
        out, count = rx.subn(repl if ctx.o["regex"] else (lambda m: repl), text)
    except (re.error, IndexError) as exc:
        raise UtilError(f"Ошибка в замене: {exc}", f"Replacement error: {exc}")
    return {"text": out, "note": ctx.tr(f"Замен: {count}", f"Replacements: {count}")}

# ── statistics ──────────────────────────────────────────────────────────────────────────────────────────────────────
_STOP = set("и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было вот от меня "
            "еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж вам ведь там "
            "потом себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже "
            "себе под будет ж тогда кто этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы нее "
            "это эта эти the a an and or of to in is it that for on with as was at by be this are from not but have has "
            "you he she they we i his her its their our your".split())

@utility("text.stats", "text", "Статистика текста", "Text statistics",
         "Символы, слова, предложения, абзацы, время чтения, частые слова и плотность ключевых слов.",
         "Characters, words, sentences, paragraphs, reading time, top words and keyword density.",
         opts=[O("top", "int", "Частых слов", "Top words", 15, min=0, max=100)], live=True, ai_default=True,
         keys="статистика символы слова количество знаков count words characters")
def text_stats(ctx):
    text = ctx.need_text()
    words = re.findall(r"[^\W_]+(?:[-'’][^\W_]+)*", text)
    sentences = [s for s in re.split(r"(?<=[.!?…])\s+", text.strip()) if s.strip()]
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    letters = sum(ch.isalpha() for ch in text)
    info = [
        [ctx.tr("Символов", "Characters"), len(text)],
        [ctx.tr("Без пробелов", "Without spaces"), len(re.sub(r"\s", "", text))],
        [ctx.tr("Букв", "Letters"), letters],
        [ctx.tr("Цифр", "Digits"), sum(ch.isdigit() for ch in text)],
        [ctx.tr("Слов", "Words"), len(words)],
        [ctx.tr("Уникальных слов", "Unique words"), len({w.lower() for w in words})],
        [ctx.tr("Предложений", "Sentences"), len(sentences)],
        [ctx.tr("Абзацев", "Paragraphs"), len(paragraphs)],
        [ctx.tr("Строк", "Lines"), len(lines_of(text))],
        [ctx.tr("Средняя длина слова", "Average word length"), round(sum(map(len, words)) / len(words), 1) if words else 0],
        [ctx.tr("Чтение (~200 сл/мин)", "Reading (~200 wpm)"), _minutes(len(words) / 200, ctx.en)],
        [ctx.tr("Вслух (~130 сл/мин)", "Speaking (~130 wpm)"), _minutes(len(words) / 130, ctx.en)],
        [ctx.tr("Страниц A4 (~1800 зн.)", "A4 pages (~1800 chars)"), round(len(text) / 1800, 1)],
    ]
    top = int(ctx.o["top"])
    out: Dict[str, Any] = {"info": info}
    if top and words:
        counts = Counter(w.lower() for w in words if w.lower() not in _STOP and not w.isdigit() and len(w) > 1)
        total = max(1, len(words))
        out["table"] = table([ctx.tr("Слово", "Word"), ctx.tr("Раз", "Times"), "%"],
                             [[w, n, f"{n * 100 / total:.1f}"] for w, n in counts.most_common(top)])
    return out

def _minutes(value: float, en: bool) -> str:
    seconds = int(round(value * 60))
    if seconds < 60:
        return f"{seconds} s" if en else f"{seconds} с"
    return (f"{seconds // 60} min {seconds % 60:02d} s" if en else f"{seconds // 60} мин {seconds % 60:02d} с")

# ── transliteration & keyboard layout ───────────────────────────────────────────────────────────────────────────────
_GOST = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                 ["a", "b", "v", "g", "d", "e", "yo", "zh", "z", "i", "j", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u",
                  "f", "x", "c", "ch", "sh", "shh", "``", "y'", "`", "e`", "yu", "ya"]))
_ICAO = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                 ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "i", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u",
                  "f", "kh", "ts", "ch", "sh", "shch", "ie", "y", "", "e", "iu", "ia"]))
_SIMPLE = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                   ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u",
                    "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))
_UK = {"є": "ie", "і": "i", "ї": "i", "ґ": "g"}
_BACK = [("shch", "щ"), ("sch", "щ"), ("yo", "ё"), ("zh", "ж"), ("kh", "х"), ("ts", "ц"), ("ch", "ч"), ("sh", "ш"),
         ("yu", "ю"), ("ya", "я"), ("ye", "е"), ("iu", "ю"), ("ia", "я"), ("a", "а"), ("b", "б"), ("v", "в"), ("g", "г"),
         ("d", "д"), ("e", "е"), ("z", "з"), ("i", "и"), ("y", "ы"), ("j", "й"), ("k", "к"), ("l", "л"), ("m", "м"),
         ("n", "н"), ("o", "о"), ("p", "п"), ("r", "р"), ("s", "с"), ("t", "т"), ("u", "у"), ("f", "ф"), ("h", "х"),
         ("c", "ц"), ("w", "в"), ("x", "кс"), ("q", "к"), ("'", "ь")]

def translit(text: str, table_name: str = "simple") -> str:
    mapping = {"gost": _GOST, "icao": _ICAO}.get(table_name, _SIMPLE)
    out = []
    for ch in text:
        low = ch.lower()
        rep = mapping.get(low, _UK.get(low))
        if rep is None:
            out.append(ch)
        elif ch != low:
            out.append(rep[:1].upper() + rep[1:] if rep else "")
        else:
            out.append(rep)
    return "".join(out)

def untranslit(text: str) -> str:
    out, i, low = [], 0, text.lower()
    while i < len(text):
        for lat, cyr in _BACK:
            if low.startswith(lat, i):
                piece = text[i:i + len(lat)]
                out.append(cyr.upper() if piece[:1].isupper() else cyr)
                i += len(lat)
                break
        else:
            out.append(text[i])
            i += 1
    return "".join(out)

@utility("text.translit", "text", "Транслитерация", "Transliteration",
         "Кириллица → латиница (простая, ГОСТ 7.79, как в загранпаспорте) и обратно.",
         "Cyrillic → Latin (simple, GOST 7.79, passport/ICAO) and back.",
         opts=[O("dir", "choice", "Направление", "Direction", "to_lat",
                 choices=[("to_lat", "Кириллица → латиница", "Cyrillic → Latin"), ("to_cyr", "Латиница → кириллица", "Latin → Cyrillic")]),
               O("table", "choice", "Система", "System", "simple", show="dir=to_lat",
                 choices=[("simple", "Простая (для URL и имён файлов)", "Simple (URLs, file names)"),
                          ("icao", "Загранпаспорт (ICAO)", "Passport (ICAO)"), ("gost", "ГОСТ 7.79-2000, система Б", "GOST 7.79-2000 B")])],
         live=True, ai_default=True, keys="транслит латиница кириллица translit cyrillic latin")
def text_translit(ctx):
    text = ctx.need_text()
    return translit(text, ctx.o["table"]) if ctx.o["dir"] == "to_lat" else untranslit(text)

_EN = "`qwertyuiop[]asdfghjkl;'zxcvbnm,./~QWERTYUIOP{}ASDFGHJKL:\"ZXCVBNM<>?@#$^&"
_RU = "ёйцукенгшщзхъфывапролджэячсмитьбю.ЁЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ,\"№;:?"
_TO_RU = {a: b for a, b in zip(_EN, _RU)}
_TO_EN = {b: a for a, b in zip(_EN, _RU)}

@utility("text.layout", "text", "Исправить раскладку (ghbdtn → привет)", "Fix keyboard layout",
         "Текст, набранный не в той раскладке: ghbdtn → привет и руддщ → hello. Направление определяется само.",
         "Text typed in the wrong layout: ghbdtn → привет and руддщ → hello. The direction is detected.",
         opts=[O("dir", "choice", "Направление", "Direction", "auto",
                 choices=[("auto", "Автоматически", "Automatic"), ("to_ru", "EN → RU", "EN → RU"), ("to_en", "RU → EN", "RU → EN")])],
         live=True, ai_default=True, keys="раскладка punto ghbdtn keyboard layout")
def text_layout(ctx):
    text, mode = ctx.need_text(), ctx.o["dir"]
    if mode == "auto":
        latin = sum(ch in _TO_RU and ch.isalpha() for ch in text)
        cyr = sum("а" <= ch.lower() <= "я" or ch in "ёЁ" for ch in text)
        mode = "to_ru" if latin >= cyr else "to_en"
    mapping = _TO_RU if mode == "to_ru" else _TO_EN
    return "".join(mapping.get(ch, ch) for ch in text)

# ── cleaning & typography ───────────────────────────────────────────────────────────────────────────────────────────
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\u200d\ufe0f\u2B00-\u2BFF\u2300-\u23FF]")

@utility("text.clean", "text", "Очистка текста", "Clean up text",
         "Лишние пробелы, пустые строки, HTML-теги, эмодзи, невидимые символы, «умные» кавычки.",
         "Extra spaces, blank lines, HTML tags, emoji, invisible characters, smart quotes.",
         opts=[O("spaces", "bool", "Сжать повторные пробелы", "Collapse repeated spaces", True),
               O("blank", "bool", "Не больше одной пустой строки", "At most one blank line", True),
               O("trim", "bool", "Обрезать пробелы в строках", "Trim each line", True),
               O("tags", "bool", "Убрать HTML-теги", "Strip HTML tags", False),
               O("emoji", "bool", "Убрать эмодзи", "Remove emoji", False),
               O("invisible", "bool", "Убрать невидимые символы (zero-width, мягкие переносы)", "Remove invisible characters", True),
               O("nbsp", "bool", "Неразрывные пробелы → обычные", "Non-breaking spaces → spaces", True),
               O("quotes", "bool", "«Умные» кавычки и тире → простые", "Smart quotes and dashes → plain", False),
               O("unwrap", "bool", "Склеить строки внутри абзацев (текст из PDF)", "Join lines inside paragraphs (PDF text)", False)],
         live=True, ai_default=True, keys="очистка пробелы теги emoji clean whitespace strip tags pdf")
def text_clean(ctx):
    text = ctx.need_text().replace("\r\n", "\n").replace("\r", "\n")
    o = ctx.o
    if o["tags"]:
        text = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", text)
        text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", text)
        text = html.unescape(re.sub(r"<[^>]+>", "", text))
    if o["invisible"]:
        text = re.sub("[\u200b-\u200d\u2060\ufeff\u00ad]", "", text)
        text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch) not in ("Cc", "Cf") or ch == "\u200d")
    if o["nbsp"]:
        text = text.replace("\u00a0", " ").replace("\u202f", " ").replace("\u2009", " ")
    if o["emoji"]:
        text = _EMOJI.sub("", text)
    if o["quotes"]:
        for a, b in (("«", '"'), ("»", '"'), ("“", '"'), ("”", '"'), ("„", '"'), ("‘", "'"), ("’", "'"), ("—", "-"),
                     ("–", "-"), ("…", "...")):
            text = text.replace(a, b)
    if o["unwrap"]:
        text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
        text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    if o["spaces"]:
        text = re.sub(r"[ \t]{2,}", " ", text)
    if o["trim"]:
        text = "\n".join(line.strip() for line in text.split("\n"))
    if o["blank"]:
        text = re.sub(r"\n{3,}", "\n\n", text).strip("\n")
    return text

@utility("text.typograph", "text", "Типограф", "Typographer",
         "Русская типографика: «ёлочки», длинное тире, неразрывные пробелы после предлогов, …, ©, ×, −.",
         "Russian/English typography: proper quotes, em dashes, non-breaking spaces, ellipsis, ©, ×, −.",
         opts=[O("lang", "choice", "Язык текста", "Text language", "ru", choices=[("ru", "Русский", "Russian"), ("en", "Английский", "English")]),
               O("nbsp", "bool", "Неразрывные пробелы", "Non-breaking spaces", True),
               O("entities", "bool", "Выводить как HTML-сущности", "Output HTML entities", False)],
         live=True, keys="типограф кавычки ёлочки тире typography quotes dash")
def text_typograph(ctx):
    text, ru = ctx.need_text(), ctx.o["lang"] == "ru"
    text = re.sub(r"\.{3}", "…", text)
    text = re.sub(r"\((c|с)\)", "©", text, flags=re.I)
    text = re.sub(r"\(r\)", "®", text, flags=re.I)
    text = re.sub(r"\(tm\)", "™", text, flags=re.I)
    text = re.sub(r"(?<=\d)\s?[xх]\s?(?=\d)", "×", text)
    text = re.sub(r"(?<=\s)-(?=\d)", "−", text)
    text = re.sub(r"(^|\s)--?(\s)", "\\1—\\2", text, flags=re.M)
    text = re.sub(r"(?<=\d)-(?=\d)", "–", text)
    text = re.sub(r"\+-", "±", text)
    open_q, close_q = ("«", "»") if ru else ("“", "”")
    inner_o, inner_c = ("„", "“") if ru else ("‘", "’")
    out, depth = [], 0
    for i, ch in enumerate(text):
        if ch == '"':
            prev = text[i - 1] if i else " "
            opening = prev.isspace() or prev in "([{—-„«“" or i == 0
            if opening:
                out.append(open_q if depth == 0 else inner_o)
                depth += 1
            else:
                depth = max(0, depth - 1)
                out.append(close_q if depth == 0 else inner_c)
        else:
            out.append(ch)
    text = "".join(out)
    if not ru:
        text = re.sub(r"(?<=\w)'(?=\w)", "’", text)
    if ctx.o["nbsp"]:
        short = r"(?:в|во|и|к|ко|с|со|у|о|об|а|на|не|ни|но|по|за|из|от|до|для|при|без|под|над|же|ли|бы)" if ru else r"(?:a|an|the|of|in|on|at|to|by|or|and|I)"
        text = re.sub(rf"(?<![\w-])({short}) (?=\S)", "\\1\u00a0", text, flags=re.I)
        text = re.sub(r" —", "\u00a0—", text)
        text = re.sub(r"(\d) (?=(?:%|руб|₽|\$|€|кг|км|м|см|мм|г|л|шт|г\.|гг\.))", "\\1\u00a0", text)
        text = re.sub(r"(№|§) ?(?=\d)", "\\1\u00a0", text)
    if ctx.o["entities"]:
        names = {"\u00a0": "&nbsp;", "«": "&laquo;", "»": "&raquo;", "—": "&mdash;", "–": "&ndash;", "…": "&hellip;",
                 "„": "&bdquo;", "“": "&ldquo;", "”": "&rdquo;", "©": "&copy;", "®": "&reg;", "™": "&trade;", "×": "&times;",
                 "−": "&minus;", "±": "&plusmn;", "’": "&rsquo;", "‘": "&lsquo;"}
        text = "".join(names.get(ch, ch) for ch in text)
    return text

# ── extraction ──────────────────────────────────────────────────────────────────────────────────────────────────────
_EXTRACT = {
    "email": r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+",
    "url": r"(?:https?://|www\.)[^\s<>\"'«»()]+[^\s<>\"'«».,;:!?()]",
    "phone": r"(?<![\w+])(?:\+?\d[\s\-()]*){10,14}\d(?!\w)",
    "number": r"(?<![\w.,])[-−]?\d+(?:[.,]\d+)?(?![\w])",
    "hashtag": r"(?<!\w)#[\w-]+",
    "mention": r"(?<![\w@])@[\w.]{2,}",
    "ip": r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])",
    "date": r"\b(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})\b",
    "money": r"(?:[$€£₽]\s?\d[\d\s.,]*\d|\d[\d\s.,]*\s?(?:₽|руб\.?|рублей|USD|EUR|\$|€))",
    "inn": r"(?<!\d)\d{10}(?:\d{2})?(?!\d)",
}

@utility("text.extract", "text", "Извлечь email, ссылки, телефоны…", "Extract emails, links, phones…",
         "Достаёт из текста все email, ссылки, телефоны, числа, хэштеги, IP, даты или суммы.",
         "Pulls every email, link, phone, number, hashtag, IP, date or amount out of a text.",
         opts=[O("what", "choice", "Что искать", "What", "email",
                 choices=[("email", "Email", "Emails"), ("url", "Ссылки", "Links"), ("phone", "Телефоны", "Phones"),
                          ("number", "Числа", "Numbers"), ("hashtag", "Хэштеги", "Hashtags"), ("mention", "Упоминания @", "Mentions"),
                          ("ip", "IP-адреса", "IP addresses"), ("date", "Даты", "Dates"), ("money", "Суммы", "Amounts"),
                          ("inn", "ИНН (10/12 цифр)", "Russian INN")]),
               O("unique", "bool", "Без повторов", "Unique only", True)],
         live=True, ai_default=True, keys="извлечь найти email ссылки телефоны extract links emails phones")
def text_extract(ctx):
    text = ctx.need_text()
    found = [m.group(0).strip() for m in re.finditer(_EXTRACT[ctx.o["what"]], text)]
    if ctx.o["unique"]:
        found = list(dict.fromkeys(found))
    return {"text": "\n".join(found), "note": ctx.tr(f"Найдено: {len(found)}", f"Found: {len(found)}")}

# ── diff ────────────────────────────────────────────────────────────────────────────────────────────────────────────
@utility("text.diff", "text", "Сравнить два текста", "Compare two texts",
         "Построчное или пословное сравнение: что добавлено, что удалено, процент сходства.",
         "Line or word diff: what was added, what was removed, and how similar the texts are.",
         input="text2", label2="Новый вариант", label2_en="New version",
         opts=[O("mode", "choice", "Сравнивать", "Compare", "lines", choices=[("lines", "Строки", "Lines"), ("words", "Слова", "Words")]),
               O("icase", "bool", "Без учёта регистра", "Ignore case", False),
               O("ispace", "bool", "Игнорировать пробелы", "Ignore whitespace", False)],
         live=True, ai_default=True, keys="сравнить разница diff compare")
def text_diff(ctx):
    a, b = ctx.text or "", ctx.text2 or ""
    if not a.strip() and not b.strip():
        raise UtilError("Введите два текста", "Enter two texts")
    norm = lambda s: (re.sub(r"\s+", " ", s).strip() if ctx.o["ispace"] else s).lower() if ctx.o["icase"] else (re.sub(r"\s+", " ", s).strip() if ctx.o["ispace"] else s)  # noqa: E731
    if ctx.o["mode"] == "words":
        ta, tb = re.findall(r"\S+|\s+", a), re.findall(r"\S+|\s+", b)
        sm = difflib.SequenceMatcher(None, [norm(x) for x in ta], [norm(x) for x in tb], autojunk=False)
        out, added, removed = [], 0, 0
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                out.append("".join(ta[i1:i2]))
                continue
            if tag in ("delete", "replace"):
                out.append("[-" + "".join(ta[i1:i2]) + "-]")
                removed += sum(1 for x in ta[i1:i2] if x.strip())
            if tag in ("insert", "replace"):
                out.append("{+" + "".join(tb[j1:j2]) + "+}")
                added += sum(1 for x in tb[j1:j2] if x.strip())
        ratio = sm.ratio()
        return {"text": "".join(out), "note": ctx.tr(f"Сходство {ratio:.0%} · добавлено слов: {added} · удалено: {removed}",
                                                     f"Similarity {ratio:.0%} · words added: {added} · removed: {removed}")}
    la, lb = lines_of(a), lines_of(b)
    sm = difflib.SequenceMatcher(None, [norm(x) for x in la], [norm(x) for x in lb], autojunk=False)
    rows, added, removed = [], 0, 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            rows.extend("  " + x for x in la[i1:i2])
        if tag in ("delete", "replace"):
            rows.extend("- " + x for x in la[i1:i2])
            removed += i2 - i1
        if tag in ("insert", "replace"):
            rows.extend("+ " + x for x in lb[j1:j2])
            added += j2 - j1
    ratio = sm.ratio()
    return {"text": "\n".join(rows), "lang": "diff",
            "note": ctx.tr(f"Сходство {ratio:.0%} · добавлено строк: {added} · удалено: {removed}",
                           f"Similarity {ratio:.0%} · lines added: {added} · removed: {removed}")}

# ── wrap / reverse / slug ───────────────────────────────────────────────────────────────────────────────────────────
@utility("text.wrap", "text", "Перенос строк по ширине", "Wrap / unwrap lines",
         "Разбить длинные строки по N символов или, наоборот, склеить жёстко перенесённые абзацы.",
         "Wrap long lines at N characters or join hard-wrapped paragraphs.",
         opts=[O("mode", "choice", "Режим", "Mode", "wrap", choices=[("wrap", "Переносить по ширине", "Wrap"), ("unwrap", "Склеить абзацы", "Unwrap")]),
               O("width", "int", "Ширина", "Width", 80, min=10, max=1000, show="mode=wrap"),
               O("indent", "text", "Отступ каждой строки", "Line indent", "", show="mode=wrap")],
         live=True, keys="перенос ширина wrap width unwrap")
def text_wrap(ctx):
    import textwrap
    text = ctx.need_text()
    paras = re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
    if ctx.o["mode"] == "unwrap":
        return "\n\n".join(re.sub(r"\s*\n\s*", " ", re.sub(r"(\w)-\n(\w)", r"\1\2", p)).strip() for p in paras)
    width, indent = int(ctx.o["width"]), ctx.o["indent"]
    return "\n\n".join(textwrap.fill(re.sub(r"\s*\n\s*", " ", p).strip(), width=width, initial_indent=indent,
                                     subsequent_indent=indent, break_long_words=True) for p in paras)

_FLIP = dict(zip("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,!?'\"()[]{}<>_&",
                 "ɐqɔpǝɟƃɥᴉɾʞlɯuodbɹsʇnʌʍxʎz∀ᗺƆᗡƎℲ⅁HIſꓘ˥WNOԀῸᴚS⊥∩ΛMX⅄Z0ƖᄅƐㄣϛ9ㄥ86˙'¡¿,„)(][}{><‾⅋"))
_FLIP.update(dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя", "ɐƍʚɹɓǝǝжεииʞvwноudɔɯʎфхǹҺmmqıqєoʁ")))

@utility("text.reverse", "text", "Перевернуть текст", "Reverse text",
         "Задом наперёд по символам, словам или строкам, а ещё «вверх ногами» (ʎɯǝʞɔ ɯɔʞǝʇ).",
         "Backwards by characters, words or lines, or upside down (ʇxǝʇ uʍop ǝpısdn).",
         opts=[O("mode", "choice", "Как", "How", "chars", choices=[("chars", "По символам", "Characters"), ("words", "По словам", "Words"),
                                                              ("lines", "По строкам", "Lines"), ("flip", "Вверх ногами", "Upside down")])],
         live=True, keys="перевернуть задом наперёд reverse upside down")
def text_reverse(ctx):
    text, mode = ctx.need_text(), ctx.o["mode"]
    if mode == "chars":
        return text[::-1]
    if mode == "words":
        return "\n".join(" ".join(line.split(" ")[::-1]) for line in lines_of(text))
    if mode == "lines":
        return "\n".join(lines_of(text)[::-1])
    return "".join(_FLIP.get(ch, _FLIP.get(ch.lower(), ch)) for ch in reversed(text))

@utility("text.slug", "text", "Slug для URL и имён файлов", "Slug for URLs and file names",
         "«Привет, мир!» → privet-mir. Транслитерация, разделитель, длина.",
         "“Hello, World!” → hello-world. Transliteration, separator, length.",
         opts=[O("sep", "choice", "Разделитель", "Separator", "-", choices=[("-", "дефис -", "hyphen -"), ("_", "подчёркивание _", "underscore _"), (".", "точка .", "dot .")]),
               O("lower", "bool", "Строчными", "Lower case", True), O("max", "int", "Макс. длина (0 — без ограничения)", "Max length (0 = none)", 0, min=0, max=500)],
         live=True, ai_default=True, keys="slug url чпу имя файла транслит")
def text_slug(ctx):
    rows = []
    for line in lines_of(ctx.need_text()):
        if not line.strip():
            continue
        value = translit(line, "simple")
        value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
        value = re.sub(r"[^A-Za-z0-9]+", ctx.o["sep"], value).strip(ctx.o["sep"])
        if ctx.o["lower"]:
            value = value.lower()
        if ctx.o["max"]:
            value = value[:int(ctx.o["max"])].rstrip(ctx.o["sep"])
        rows.append(value)
    return "\n".join(rows)

# ── numbers in words ────────────────────────────────────────────────────────────────────────────────────────────────
_RU_UNITS = ["", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
_RU_UNITS_F = ["", "одна", "две", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
_RU_TEENS = ["десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать", "шестнадцать",
             "семнадцать", "восемнадцать", "девятнадцать"]
_RU_TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто"]
_RU_HUNDREDS = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот"]
_RU_SCALES = [(("", "", ""), False), (("тысяча", "тысячи", "тысяч"), True), (("миллион", "миллиона", "миллионов"), False),
              (("миллиард", "миллиарда", "миллиардов"), False), (("триллион", "триллиона", "триллионов"), False),
              (("квадриллион", "квадриллиона", "квадриллионов"), False)]

def ru_plural(n: int, forms) -> str:
    n = abs(n) % 100
    if 11 <= n <= 19:
        return forms[2]
    n %= 10
    return forms[0] if n == 1 else forms[1] if 2 <= n <= 4 else forms[2]

def _ru_triad(n: int, female: bool) -> List[str]:
    words = [_RU_HUNDREDS[n // 100]]
    rest = n % 100
    if 10 <= rest <= 19:
        words.append(_RU_TEENS[rest - 10])
    else:
        words += [_RU_TENS[rest // 10], (_RU_UNITS_F if female else _RU_UNITS)[rest % 10]]
    return [w for w in words if w]

def ru_words(n: int, female: bool = False) -> str:
    if n == 0:
        return "ноль"
    words, scale = [], 0
    neg, n = n < 0, abs(n)
    while n:
        n, triad = divmod(n, 1000)
        if triad:
            forms, fem = _RU_SCALES[scale] if scale < len(_RU_SCALES) else (("", "", ""), False)
            part = _ru_triad(triad, fem if scale else female)
            if scale:
                part.append(ru_plural(triad, forms))
            words = part + words
        scale += 1
    return ("минус " if neg else "") + " ".join(words)

_EN_UNITS = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_EN_TENS = "  twenty thirty forty fifty sixty seventy eighty ninety".split(" ")

def en_words(n: int) -> str:
    if n < 0:
        return "minus " + en_words(-n)
    if n < 20:
        return _EN_UNITS[n]
    if n < 100:
        return _EN_TENS[n // 10] + ("-" + _EN_UNITS[n % 10] if n % 10 else "")
    if n < 1000:
        return _EN_UNITS[n // 100] + " hundred" + (" " + en_words(n % 100) if n % 100 else "")
    for value, name in ((10 ** 15, "quadrillion"), (10 ** 12, "trillion"), (10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand")):
        if n >= value:
            head, rest = divmod(n, value)
            return en_words(head) + " " + name + (" " + en_words(rest) if rest else "")
    return str(n)

@utility("text.numwords", "text", "Число прописью", "Number in words",
         "1234,56 → «одна тысяча двести тридцать четыре рубля 56 копеек». Русский и английский, суммы для документов.",
         "1234.56 → “one thousand two hundred thirty-four dollars and 56 cents”. Russian and English, amounts for documents.",
         opts=[O("lang", "choice", "Язык", "Language", "ru", choices=[("ru", "Русский", "Russian"), ("en", "Английский", "English")]),
               O("money", "choice", "Валюта", "Currency", "none",
                 choices=[("none", "Просто число", "Plain number"), ("rub", "Рубли", "Rubles"), ("usd", "Доллары", "Dollars"), ("eur", "Евро", "Euros")]),
               O("cap", "bool", "С заглавной буквы", "Capitalize", True)],
         live=True, ai_default=True, placeholder="1234,56", keys="прописью сумма число словами number words amount")
def text_numwords(ctx):
    rows = []
    for line in lines_of(ctx.need_text("Введите число", "Enter a number")):
        raw = re.sub(r"[\s\u00a0_]", "", line)
        if not raw:
            continue
        m = re.fullmatch(r"([-−]?)(\d+)(?:[.,](\d+))?", raw)
        if not m:
            raise UtilError(f"«{line}» — не число", f"“{line}” is not a number")
        neg, whole, frac = m.group(1), int(m.group(2)), m.group(3) or ""
        if whole >= 10 ** 18:
            raise UtilError("Слишком большое число", "The number is too big")
        lang, money = ctx.o["lang"], ctx.o["money"]
        if lang == "ru":
            if money == "none":
                text = ru_words(whole)
                if frac:
                    f = int(frac)
                    denom = {1: ("десятая", "десятых", "десятых"), 2: ("сотая", "сотых", "сотых"),
                             3: ("тысячная", "тысячных", "тысячных")}.get(len(frac), ("", "", ""))
                    text += f" {ru_plural(whole, ('целая', 'целых', 'целых'))} {ru_words(f, True)} {ru_plural(f, denom)}".rstrip()
            else:
                major = {"rub": ("рубль", "рубля", "рублей"), "usd": ("доллар", "доллара", "долларов"), "eur": ("евро", "евро", "евро")}[money]
                minor = {"rub": ("копейка", "копейки", "копеек"), "usd": ("цент", "цента", "центов"), "eur": ("цент", "цента", "центов")}[money]
                cents = int((frac + "00")[:2])
                text = f"{ru_words(whole)} {ru_plural(whole, major)} {cents:02d} {ru_plural(cents, minor)}"
        else:
            if money == "none":
                text = en_words(whole) + (" point " + " ".join(_EN_UNITS[int(d)] for d in frac) if frac else "")
            else:
                major = {"rub": ("ruble", "rubles"), "usd": ("dollar", "dollars"), "eur": ("euro", "euros")}[money]
                minor = {"rub": ("kopeck", "kopecks"), "usd": ("cent", "cents"), "eur": ("cent", "cents")}[money]
                cents = int((frac + "00")[:2])
                text = f"{en_words(whole)} {major[0] if whole == 1 else major[1]} and {cents:02d} {minor[0] if cents == 1 else minor[1]}"
        if neg:
            text = ("минус " if lang == "ru" else "minus ") + text
        rows.append(text[:1].upper() + text[1:] if ctx.o["cap"] else text)
    return "\n".join(rows)

# ── morse ───────────────────────────────────────────────────────────────────────────────────────────────────────────
_MORSE = {"A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.", "G": "--.", "H": "....", "I": "..", "J": ".---",
          "K": "-.-", "L": ".-..", "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.", "S": "...", "T": "-",
          "U": "..-", "V": "...-", "W": ".--", "X": "-..-", "Y": "-.--", "Z": "--..", "0": "-----", "1": ".----", "2": "..---",
          "3": "...--", "4": "....-", "5": ".....", "6": "-....", "7": "--...", "8": "---..", "9": "----.", ".": ".-.-.-",
          ",": "--..--", "?": "..--..", "'": ".----.", "!": "-.-.--", "/": "-..-.", "(": "-.--.", ")": "-.--.-", "&": ".-...",
          ":": "---...", ";": "-.-.-.", "=": "-...-", "+": ".-.-.", "-": "-....-", "_": "..--.-", '"': ".-..-.", "@": ".--.-."}
_MORSE_RU = {"А": ".-", "Б": "-...", "В": ".--", "Г": "--.", "Д": "-..", "Е": ".", "Ж": "...-", "З": "--..", "И": "..", "Й": ".---",
             "К": "-.-", "Л": ".-..", "М": "--", "Н": "-.", "О": "---", "П": ".--.", "Р": ".-.", "С": "...", "Т": "-", "У": "..-",
             "Ф": "..-.", "Х": "....", "Ц": "-.-.", "Ч": "---.", "Ш": "----", "Щ": "--.-", "Ъ": "--.--", "Ы": "-.--", "Ь": "-..-",
             "Э": "..-..", "Ю": "..--", "Я": ".-.-", "Ё": "."}

@utility("text.morse", "text", "Азбука Морзе", "Morse code",
         "Текст → точки и тире и обратно, латиница и кириллица.", "Text → dots and dashes and back, Latin and Cyrillic.",
         opts=[O("dir", "choice", "Направление", "Direction", "auto", choices=[("auto", "Автоматически", "Automatic"), ("encode", "Текст → Морзе", "Text → Morse"), ("decode", "Морзе → текст", "Morse → text")]),
               O("alphabet", "choice", "Алфавит расшифровки", "Decode alphabet", "auto", show="dir=auto|decode",
                 choices=[("auto", "Как в тексте/латиница", "Latin"), ("ru", "Кириллица", "Cyrillic")])],
         live=True, keys="морзе morse")
def text_morse(ctx):
    text = ctx.need_text().strip()
    mode = ctx.o["dir"]
    if mode == "auto":
        mode = "decode" if re.fullmatch(r"[.\-·−—_/|\s]+", text) else "encode"
    if mode == "encode":
        out = []
        for word in text.upper().split():
            out.append(" ".join(_MORSE.get(ch) or _MORSE_RU.get(ch) or "" for ch in word).strip())
        return " / ".join(w for w in out if w)
    back = {v: k for k, v in (_MORSE_RU if ctx.o["alphabet"] == "ru" else _MORSE).items() if k != "Ё"}
    text = text.replace("·", ".").replace("−", "-").replace("—", "-").replace("_", "-")
    words = re.split(r"\s*[/|]\s*|\s{3,}", text)
    return " ".join("".join(back.get(code, "?") for code in word.split()) for word in words).strip()

# ── fancy unicode ───────────────────────────────────────────────────────────────────────────────────────────────────
def _alpha_map(upper_start: int, lower_start: int, digit_start: int = 0, exceptions: Dict[str, str] = None) -> Dict[str, str]:
    m = {}
    for i in range(26):
        m[chr(65 + i)] = chr(upper_start + i)
        m[chr(97 + i)] = chr(lower_start + i)
    if digit_start:
        for i in range(10):
            m[chr(48 + i)] = chr(digit_start + i)
    m.update(exceptions or {})
    return m

_FANCY = {
    "bold": _alpha_map(0x1D400, 0x1D41A, 0x1D7CE), "italic": _alpha_map(0x1D434, 0x1D44E, 0, {"h": "ℎ"}),
    "bolditalic": _alpha_map(0x1D468, 0x1D482), "script": _alpha_map(0x1D4D0, 0x1D4EA),
    "fraktur": _alpha_map(0x1D56C, 0x1D586), "double": _alpha_map(0x1D538, 0x1D552, 0x1D7D8, {"C": "ℂ", "H": "ℍ", "N": "ℕ", "P": "ℙ", "Q": "ℚ", "R": "ℝ", "Z": "ℤ"}),
    "mono": _alpha_map(0x1D670, 0x1D68A, 0x1D7F6), "sans": _alpha_map(0x1D5D4, 0x1D5EE, 0x1D7EC),
    "circled": {**{chr(65 + i): chr(0x24B6 + i) for i in range(26)}, **{chr(97 + i): chr(0x24D0 + i) for i in range(26)},
                **{str(i): chr(0x2460 + i - 1) for i in range(1, 10)}, "0": "⓪"},
    "squared": {**{chr(65 + i): chr(0x1F130 + i) for i in range(26)}, **{chr(97 + i): chr(0x1F130 + i) for i in range(26)}},
    "full": {chr(i): chr(i + 0xFEE0) for i in range(33, 127)},
    "small": dict(zip("abcdefghijklmnopqrstuvwxyz", "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢ")),
}

@utility("text.fancy", "text", "Красивые шрифты Unicode", "Fancy Unicode text",
         "𝐉𝐢𝐫𝐧𝐲𝐣, 𝑘𝑢𝑟𝑠𝑖𝑣, 𝔊𝔬𝔱𝔦𝔨𝔞, 𝕯𝖔𝖚𝖇𝖑𝖊, ⓒⓘⓡⓒⓛⓔ, ｗｉｄｅ, з̶а̶ч̶ё̶р̶к̶ — для соцсетей и ников (латиница).",
         "𝐁𝐨𝐥𝐝, 𝑖𝑡𝑎𝑙𝑖𝑐, 𝔣𝔯𝔞𝔨𝔱𝔲𝔯, 𝕕𝕠𝕦𝕓𝕝𝕖, ⓒⓘⓡⓒⓛⓔ, ｗｉｄｅ, s̶t̶r̶i̶k̶e̶ — for social posts and nicknames.",
         opts=[O("style", "choice", "Стиль", "Style", "bold",
                 choices=[("bold", "Жирный", "Bold"), ("italic", "Курсив", "Italic"), ("bolditalic", "Жирный курсив", "Bold italic"),
                          ("script", "Рукописный", "Script"), ("fraktur", "Готический", "Fraktur"), ("double", "Двойной", "Double-struck"),
                          ("mono", "Моноширинный", "Monospace"), ("sans", "Жирный без засечек", "Sans bold"), ("circled", "В кружках", "Circled"),
                          ("squared", "В квадратах", "Squared"), ("full", "Широкий", "Full-width"), ("small", "Капитель", "Small caps"),
                          ("strike", "Зачёркнутый", "Strikethrough"), ("under", "Подчёркнутый", "Underline"), ("all", "Показать все", "Show all")])],
         live=True, keys="шрифт unicode красивый текст ник fancy font bold italic")
def text_fancy(ctx):
    text, style = ctx.need_text(), ctx.o["style"]

    def apply(name: str) -> str:
        if name == "strike":
            return "".join(ch + "\u0336" if not ch.isspace() else ch for ch in text)
        if name == "under":
            return "".join(ch + "\u0332" if not ch.isspace() else ch for ch in text)
        mapping = _FANCY[name]
        return "".join(mapping.get(ch, ch) for ch in text)
    if style == "all":
        return "\n".join(apply(name) for name in list(_FANCY) + ["strike", "under"])
    return apply(style)

# ── tokens / chunks ─────────────────────────────────────────────────────────────────────────────────────────────────
def estimate_tokens(text: str) -> int:
    latin = len(re.findall(r"[A-Za-z]", text))
    cyr = len(re.findall(r"[А-Яа-яЁё]", text))
    cjk = len(re.findall(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", text))
    digits = len(re.findall(r"\d", text))
    other = max(0, len(text) - latin - cyr - cjk - digits - len(re.findall(r"\s", text)))
    words = len(re.findall(r"\S+", text))
    return int(round(latin / 4.2 + cyr / 2.6 + cjk * 1.1 + digits / 2.5 + other * 0.8 + words * 0.15))

@utility("text.tokens", "text", "Токены и разбиение на части", "Tokens and chunks",
         "Примерная оценка токенов для нейросетей и разбиение длинного текста на части по N токенов/символов.",
         "Estimate LLM tokens and split a long text into chunks of N tokens/characters.",
         opts=[O("mode", "choice", "Действие", "Action", "count", choices=[("count", "Оценить токены", "Estimate tokens"), ("chunk", "Разбить на части", "Split into chunks")]),
               O("size", "int", "Размер части (токенов)", "Chunk size (tokens)", 2000, min=50, max=200000, show="mode=chunk"),
               O("overlap", "int", "Перекрытие (токенов)", "Overlap (tokens)", 0, min=0, max=5000, show="mode=chunk")],
         live=True, keys="токены tokens chunk gpt llm разбить части")
def text_tokens(ctx):
    text = ctx.need_text()
    total = estimate_tokens(text)
    if ctx.o["mode"] == "count":
        return {"info": [[ctx.tr("Токенов (оценка)", "Tokens (estimate)"), total], [ctx.tr("Символов", "Characters"), len(text)],
                         [ctx.tr("Слов", "Words"), len(text.split())],
                         [ctx.tr("Символов на токен", "Characters per token"), round(len(text) / max(1, total), 2)]],
                "note": ctx.tr("Точное число зависит от модели; погрешность ±15%.", "The exact count depends on the model; ±15%.")}
    size, overlap = int(ctx.o["size"]), min(int(ctx.o["overlap"]), int(ctx.o["size"]) // 2)
    ratio = len(text) / max(1, total)
    csize, cover = max(50, int(size * ratio)), int(overlap * ratio)
    parts, start = [], 0
    while start < len(text):
        end = min(len(text), start + csize)
        if end < len(text):
            cut = max(text.rfind("\n\n", start, end), text.rfind(". ", start, end))
            if cut > start + csize // 2:
                end = cut + 1
        parts.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - cover, start + 1)
    sep = "\n\n" + "─" * 24 + "\n\n"
    return {"text": sep.join(f"[{i}/{len(parts)}]\n{p}" for i, p in enumerate(parts, 1)),
            "note": ctx.tr(f"Частей: {len(parts)} · всего ~{total} токенов", f"Chunks: {len(parts)} · ~{total} tokens in total")}

# ── mojibake ────────────────────────────────────────────────────────────────────────────────────────────────────────
@utility("text.mojibake", "text", "Починить «кракозябры»", "Fix mojibake",
         "РџСЂРёРІРµС‚ или Ð¿Ñ€Ð¸Ð²ÐµÑ‚ → привет: подбирает, в какой кодировке текст сломали.",
         "РџСЂРёРІРµС‚ or Ð¿Ñ€Ð¸Ð²ÐµÑ‚ → привет: finds the encoding mix-up that broke the text.",
         live=True, ai_default=True, keys="кракозябры кодировка mojibake encoding cp1251 utf-8 абракадабра")
def text_mojibake(ctx):
    text = ctx.need_text()
    best, best_score, how = text, _cyr_quality(text), ""
    for wrong in ("cp1251", "latin-1", "cp1252", "koi8-r", "cp866", "mac_cyrillic"):
        for right in ("utf-8", "cp1251", "koi8-r", "cp866"):
            if wrong == right:
                continue
            try:
                fixed = text.encode(wrong).decode(right)
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
            score = _cyr_quality(fixed)
            if score > best_score + 0.05:
                best, best_score, how = fixed, score, f"{wrong} → {right}"
    if not how:
        return {"text": text, "note": ctx.tr("Текст выглядит нормально — чинить нечего.", "The text looks fine — nothing to fix.")}
    return {"text": best, "note": ctx.tr(f"Исправлено: прочитан как {how}", f"Fixed: read as {how}")}

def _cyr_quality(text: str) -> float:
    if not text:
        return 0.0
    letters = sum(ch.isalpha() for ch in text)
    good = len(re.findall(r"[а-яё]", text)) + len(re.findall(r"[a-z]", text)) + len(re.findall(r"[А-ЯЁA-Z]", text)) * 0.5
    bad = len(re.findall(r"[ÐÑÃâ€™Ð°-ÿ\u0080-\u00bfЃЉЊЌЋЏђ‘’“”•–—™љ›њќћџЎўЈ¤Ґ¦§Ё©Є«¬®Ї°±Ііґµ¶·ё№є»јЅѕї]", text))
    return (good - bad * 2) / max(1, letters)

# ── template (mail merge) ───────────────────────────────────────────────────────────────────────────────────────────
@utility("text.template", "text", "Шаблон по таблице (рассылка)", "Template × table (mail merge)",
         "Шаблон с {полями} + CSV-таблица → текст для каждой строки. Удобно для писем, поздравлений, карточек.",
         "A template with {fields} + a CSV table → one text per row. Handy for letters, greetings, cards.",
         input="text2", label2="Таблица CSV (первая строка — названия полей)", label2_en="CSV table (first row = field names)",
         opts=[O("sep", "text", "Разделитель результатов", "Result separator", "\\n\\n---\\n\\n")],
         placeholder="Здравствуйте, {имя}! Ваш заказ №{номер} готов.", placeholder_en="Hi {name}! Your order #{number} is ready.",
         keys="шаблон рассылка mail merge template csv")
def text_template(ctx):
    import csv
    import io
    template = ctx.need_text("Введите шаблон с {полями}", "Enter a template with {fields}")
    data = (ctx.text2 or "").strip()
    if not data:
        raise UtilError("Вставьте таблицу CSV во второе поле", "Paste a CSV table into the second box")
    dialect = csv.Sniffer().sniff(data[:4000], delimiters=",;\t|") if data else csv.excel
    rows = list(csv.DictReader(io.StringIO(data), dialect=dialect))
    out = []
    for row in rows:
        clean = {str(k).strip(): (v or "").strip() for k, v in row.items() if k is not None}
        out.append(re.sub(r"\{([^{}]+)\}", lambda m: clean.get(m.group(1).strip(), m.group(0)), template))
    sep = ctx.o["sep"].replace("\\n", "\n").replace("\\t", "\t")
    return {"text": sep.join(out), "note": ctx.tr(f"Готово текстов: {len(out)}", f"Texts made: {len(out)}")}
