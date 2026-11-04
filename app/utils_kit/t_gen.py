# -*- coding: utf-8 -*-
"""Generators: UUID/ULID, passwords, lorem ipsum, fake people, placeholder images, QR codes, barcodes, palettes…"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import random
import re
import secrets
import string
import struct
import time
import uuid as _uuid
from datetime import date, timedelta
from typing import Any, Dict, List, Tuple

from .core import O, UtilError, lines_of, parse_color, table, utility

_RND = random.SystemRandom()

# ── UUID / ULID / nanoid / tokens ───────────────────────────────────────────────────────────────────────────────────
_CROCK = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

def ulid() -> str:
    ms = int(time.time() * 1000)
    head = "".join(_CROCK[(ms >> (5 * i)) & 31] for i in reversed(range(10)))
    return head + "".join(_RND.choice(_CROCK) for _ in range(16))

def uuid7() -> str:
    ms = int(time.time() * 1000)
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ms << 80) | (0x7 << 76) | ((rand >> 62) & 0xFFF) << 64 | (0b10 << 62) | (rand & ((1 << 62) - 1))
    return str(_uuid.UUID(int=value))

@utility("gen.uuid", "gen", "UUID, ULID, nanoid", "UUID, ULID, nanoid",
         "Уникальные идентификаторы: UUID v4/v7/v1, ULID, nanoid, ObjectId — пачкой, в нужном регистре.",
         "Unique IDs: UUID v4/v7/v1, ULID, nanoid, ObjectId — in batches, in the case you want.",
         input="none", opts=[O("kind", "choice", "Тип", "Type", "v4", choices=[("v4", "UUID v4 (случайный)", "UUID v4 (random)"), ("v7", "UUID v7 (по времени, сортируемый)", "UUID v7 (time-ordered)"),
                                                                    ("v1", "UUID v1", "UUID v1"), ("ulid", "ULID", "ULID"), ("nanoid", "nanoid (21)", "nanoid (21)"),
                                                                    ("objectid", "MongoDB ObjectId", "MongoDB ObjectId"), ("short", "Короткий (8 символов)", "Short (8 chars)")]),
                             O("count", "int", "Сколько", "How many", 5, min=1, max=10000), O("upper", "bool", "Заглавными", "Upper case", False),
                             O("nodash", "bool", "Без дефисов", "No hyphens", False, show="kind=v4|v7|v1")],
         live=True, ai_default=True, keys="uuid guid ulid nanoid id идентификатор сгенерировать")
def gen_uuid(ctx):
    kind, n = ctx.o["kind"], int(ctx.o["count"])
    alphabet = string.ascii_letters + string.digits + "_-"
    make = {"v4": lambda: str(_uuid.uuid4()), "v7": uuid7, "v1": lambda: str(_uuid.uuid1()), "ulid": ulid,
            "nanoid": lambda: "".join(_RND.choice(alphabet) for _ in range(21)),
            "objectid": lambda: f"{int(time.time()):08x}" + os.urandom(8).hex(),
            "short": lambda: "".join(_RND.choice(string.ascii_lowercase + string.digits) for _ in range(8))}[kind]
    out = []
    for _ in range(n):
        v = make()
        if ctx.o["nodash"] and kind in ("v4", "v7", "v1"):
            v = v.replace("-", "")
        out.append(v.upper() if ctx.o["upper"] else v)
    return "\n".join(out)

@utility("gen.token", "gen", "Секреты и API-ключи", "Secrets and API keys",
         "Криптостойкие токены: hex, Base64url, ключи вида sk-…, PIN-коды, соль для паролей.",
         "Cryptographically secure tokens: hex, Base64url, sk-… style keys, PIN codes, salts.",
         input="none", opts=[O("kind", "choice", "Вид", "Kind", "hex", choices=[("hex", "Hex", "Hex"), ("b64", "Base64url", "Base64url"), ("key", "API-ключ sk-…", "API key sk-…"),
                                                                     ("pin", "PIN-код", "PIN code"), ("django", "Django SECRET_KEY", "Django SECRET_KEY")]),
                             O("bytes", "int", "Длина, байт (для PIN — цифр)", "Length, bytes (PIN: digits)", 32, min=4, max=512),
                             O("count", "int", "Сколько", "How many", 3, min=1, max=1000)],
         live=True, ai_default=True, keys="токен секрет ключ api key secret pin соль salt")
def gen_token(ctx):
    n, size, kind = int(ctx.o["count"]), int(ctx.o["bytes"]), ctx.o["kind"]
    make = {"hex": lambda: secrets.token_hex(size), "b64": lambda: secrets.token_urlsafe(size),
            "key": lambda: "sk-" + "".join(_RND.choice(string.ascii_letters + string.digits) for _ in range(max(16, size))),
            "pin": lambda: "".join(_RND.choice(string.digits) for _ in range(min(size, 32))),
            "django": lambda: "".join(_RND.choice(string.ascii_lowercase + string.digits + "!@#$%^&*(-_=+)") for _ in range(50))}[kind]
    return "\n".join(make() for _ in range(n))

# ── passwords ───────────────────────────────────────────────────────────────────────────────────────────────────────
@utility("gen.password", "gen", "Генератор паролей", "Password generator",
         "Надёжные пароли нужной длины: буквы, цифры, символы, без похожих (l1O0), с оценкой стойкости.",
         "Strong passwords of any length: letters, digits, symbols, without look-alikes (l1O0), with a strength estimate.",
         input="none", opts=[O("length", "int", "Длина", "Length", 20, min=4, max=256), O("count", "int", "Сколько", "How many", 5, min=1, max=500),
                             O("upper", "bool", "Заглавные A–Z", "Upper case A–Z", True), O("lower", "bool", "Строчные a–z", "Lower case a–z", True),
                             O("digits", "bool", "Цифры 0–9", "Digits 0–9", True), O("symbols", "bool", "Символы !@#…", "Symbols !@#…", True),
                             O("similar", "bool", "Без похожих символов (l, 1, O, 0)", "No look-alikes (l, 1, O, 0)", True)],
         live=True, ai_default=True, keys="пароль генератор сгенерировать password generator")
def gen_password(ctx):
    sets = []
    if ctx.o["upper"]:
        sets.append(string.ascii_uppercase)
    if ctx.o["lower"]:
        sets.append(string.ascii_lowercase)
    if ctx.o["digits"]:
        sets.append(string.digits)
    if ctx.o["symbols"]:
        sets.append("!@#$%^&*()-_=+[]{};:,.?/")
    if not sets:
        raise UtilError("Отметьте хотя бы один набор символов", "Tick at least one character set")
    if ctx.o["similar"]:
        sets = ["".join(ch for ch in s if ch not in "lI1O0o|") for s in sets]
    pool = "".join(sets)
    length = int(ctx.o["length"])
    out = []
    for _ in range(int(ctx.o["count"])):
        chars = [_RND.choice(s) for s in sets][:length] + [_RND.choice(pool) for _ in range(max(0, length - len(sets)))]
        _RND.shuffle(chars)
        out.append("".join(chars))
    bits = length * math.log2(len(pool))
    return {"text": "\n".join(out), "note": ctx.tr(f"Энтропия ≈ {bits:.0f} бит", f"Entropy ≈ {bits:.0f} bits")}

_SYL_C = "bdfgklmnprstvz"
_SYL_V = "aeiou"
_WORDS_EN = ("apple river stone cloud tiger maple ocean silver garden rocket pencil castle forest winter summer planet candle mirror "
             "bridge yellow orange purple marble copper engine island valley meadow thunder breeze shadow lantern harbor canyon falcon "
             "button cookie dragon jungle kettle ladder magnet needle pepper quartz rabbit saddle tunnel velvet wizard zebra anchor "
             "basket cactus dolphin feather glacier helmet igloo jacket koala lemon mango nectar olive parrot quiver raven sunset "
             "tomato umbrella violin walnut yogurt cobalt ember frost gravel horizon ivory jasmine lagoon mosaic nebula orbit pixel").split()
_WORDS_RU = ("kot dom les reka gora more sneg veter solnce luna zvezda most sad pole put gorod sok med chai hleb sol "
             "sova lisa volk medved zayac ezh ryba ptica kit slon tigr enot belka utka gus lebed orel sokol "
             "kniga ruchka stol stul okno dver lampa chasy klyuch zamok karta kompas fonar parus yakor korabl poezd").split()

@utility("gen.passphrase", "gen", "Парольная фраза", "Passphrase generator",
         "Запоминаемые пароли из слов: correct-horse-battery-staple, с цифрой и заглавной по желанию.",
         "Memorable passwords from words: correct-horse-battery-staple, with a digit and capitals if you like.",
         input="none", opts=[O("words", "int", "Слов", "Words", 4, min=2, max=12), O("sep", "text", "Разделитель", "Separator", "-"),
                             O("dict", "choice", "Словарь", "Word list", "en", choices=[("en", "Английские слова", "English words"), ("ru", "Русские слова латиницей", "Russian words in Latin"),
                                                                              ("syll", "Произносимые слоги", "Pronounceable syllables")]),
                             O("cap", "bool", "С заглавной", "Capitalize", True), O("digit", "bool", "Добавить число", "Add a number", True),
                             O("count", "int", "Сколько", "How many", 5, min=1, max=200)],
         live=True, keys="парольная фраза passphrase diceware слова пароль")
def gen_passphrase(ctx):
    out = []
    for _ in range(int(ctx.o["count"])):
        words = []
        for _ in range(int(ctx.o["words"])):
            if ctx.o["dict"] == "syll":
                w = "".join(_RND.choice(_SYL_C) + _RND.choice(_SYL_V) for _ in range(_RND.randint(2, 3)))
            else:
                w = _RND.choice(_WORDS_EN if ctx.o["dict"] == "en" else _WORDS_RU)
            words.append(w.capitalize() if ctx.o["cap"] else w)
        if ctx.o["digit"]:
            words.append(str(_RND.randint(10, 99)))
        out.append(ctx.o["sep"].join(words))
    pool = len(_WORDS_EN if ctx.o["dict"] == "en" else _WORDS_RU) if ctx.o["dict"] != "syll" else 70 ** 2.5
    bits = int(ctx.o["words"]) * math.log2(pool) + (6.5 if ctx.o["digit"] else 0)
    return {"text": "\n".join(out), "note": ctx.tr(f"Энтропия ≈ {bits:.0f} бит", f"Entropy ≈ {bits:.0f} bits")}

# ── lorem ipsum ─────────────────────────────────────────────────────────────────────────────────────────────────────
_LOREM = ("lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor incididunt ut labore et dolore magna aliqua "
          "enim ad minim veniam quis nostrud exercitation ullamco laboris nisi aliquip ex ea commodo consequat duis aute irure in "
          "reprehenderit voluptate velit esse cillum fugiat nulla pariatur excepteur sint occaecat cupidatat non proident sunt culpa qui "
          "officia deserunt mollit anim id est laborum curabitur pretium tincidunt lacus nunc pulvinar sapien ligula vitae mauris "
          "integer vel faucibus orci luctus ultrices posuere cubilia curae morbi tristique senectus netus malesuada fames ac turpis").split()
_RU_FISH = ("далеко за словесными горами в стране гласных и согласных живут рыбные тексты вдали от всех они живут в буквенных домах "
            "на берегу семантика большого языкового океана маленький ручеёк даль журчит по всей стране и обеспечивает её всеми "
            "необходимыми правилами эта парадигматическая страна в которой жаренные члены предложения залетают прямо в рот даже "
            "всемогущая пунктуация не имеет власти над рыбными текстами ведущими безорфографичный образ жизни однажды одна маленькая "
            "строчка рыбного текста по имени решила выйти в большой мир грамматики великий оксмокс предупреждал её о злых запятых "
            "диких знаках вопроса и коварных точках с запятой но текст не дал сбить себя с толку").split()
_EN_WORDS = ("the quick brown fox jumps over lazy dog while bright morning light falls across quiet city streets people walk to work "
             "coffee shops open their doors and the day begins with small plans big ideas and a little bit of luck every team "
             "builds something new learns from mistakes shares results and moves forward step by step").split()

def lorem_sentence(words: List[str], lo: int = 6, hi: int = 14) -> str:
    n = _RND.randint(lo, hi)
    s = " ".join(_RND.choice(words) for _ in range(n))
    if n > 8 and _RND.random() < 0.5:
        parts = s.split(" ")
        k = _RND.randint(3, n - 3)
        parts[k] += ","
        s = " ".join(parts)
    return s[:1].upper() + s[1:] + "."

@utility("gen.lorem", "gen", "Текст-рыба (Lorem ipsum)", "Placeholder text (Lorem ipsum)",
         "Lorem ipsum на латыни, русская «рыба» или английский текст — абзацами, предложениями или словами, можно в <p>.",
         "Lorem ipsum in Latin, Russian “fish” text or English — by paragraphs, sentences or words, optionally in <p>.",
         input="none", opts=[O("lang", "choice", "Язык", "Language", "la", choices=[("la", "Латынь (Lorem ipsum)", "Latin (Lorem ipsum)"), ("ru", "Русский", "Russian"), ("en", "Английский", "English")]),
                             O("unit", "choice", "Единица", "Unit", "para", choices=[("para", "Абзацы", "Paragraphs"), ("sent", "Предложения", "Sentences"), ("word", "Слова", "Words")]),
                             O("count", "int", "Количество", "Count", 3, min=1, max=1000), O("html", "bool", "Обернуть в <p>", "Wrap in <p>", False),
                             O("classic", "bool", "Начать с «Lorem ipsum dolor sit amet…»", "Start with “Lorem ipsum dolor sit amet…”", True, show="lang=la")],
         live=True, ai_default=True, keys="lorem ipsum рыба текст заглушка placeholder text")
def gen_lorem(ctx):
    words = {"la": _LOREM, "ru": _RU_FISH, "en": _EN_WORDS}[ctx.o["lang"]]
    n, unit = int(ctx.o["count"]), ctx.o["unit"]
    if unit == "word":
        out = " ".join(_RND.choice(words) for _ in range(n))
        return out[:1].upper() + out[1:]
    if unit == "sent":
        paras = [" ".join(lorem_sentence(words) for _ in range(n))]
    else:
        paras = [" ".join(lorem_sentence(words) for _ in range(_RND.randint(4, 7))) for _ in range(n)]
    if ctx.o["lang"] == "la" and ctx.o["classic"]:
        paras[0] = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " + paras[0]
    if ctx.o["html"]:
        return "\n".join(f"<p>{p}</p>" for p in paras)
    return "\n\n".join(paras)

# ── fake people ─────────────────────────────────────────────────────────────────────────────────────────────────────
_RU_M = "Александр Дмитрий Максим Сергей Андрей Алексей Артём Илья Кирилл Михаил Никита Матвей Роман Егор Иван Павел Денис Владимир Николай Олег".split()
_RU_F = "Анна Мария Елена Ольга Наталья Екатерина Анастасия Татьяна Ирина Дарья Юлия Полина Виктория Алиса Софья Ксения Валерия Вера Светлана Марина".split()
_RU_L = "Иванов Смирнов Кузнецов Попов Васильев Петров Соколов Михайлов Новиков Фёдоров Морозов Волков Алексеев Лебедев Семёнов Егоров Павлов Козлов Степанов Николаев Орлов Андреев Макаров Никитин Захаров".split()
_RU_P = "Александрович Дмитриевич Сергеевич Андреевич Алексеевич Михайлович Иванович Николаевич Владимирович Павлович".split()
_RU_CITY = "Москва Санкт-Петербург Новосибирск Екатеринбург Казань Нижний\u00a0Новгород Челябинск Самара Омск Ростов-на-Дону Уфа Красноярск Воронеж Пермь Волгоград Краснодар Тюмень Иркутск".split()
_RU_STREET = "Ленина Мира Советская Садовая Лесная Школьная Молодёжная Центральная Новая Набережная Гагарина Пушкина Кирова Октябрьская Победы".split()
_RU_CO = "Вектор Альфа Сигма Горизонт Ромашка Технопром Прогресс Север Восход Меридиан Гранит Квант Импульс Стройинвест Интеграл".split()
_RU_JOB = "Менеджер Бухгалтер Инженер Программист Дизайнер Аналитик Юрист Маркетолог Врач Учитель Логист Технолог Архитектор Продавец".split()
_EN_M = "James John Robert Michael William David Richard Joseph Thomas Charles Daniel Matthew Anthony Mark Steven Paul Andrew Joshua Kevin Brian".split()
_EN_F = "Mary Patricia Jennifer Linda Elizabeth Barbara Susan Jessica Sarah Karen Lisa Nancy Betty Sandra Ashley Emily Olivia Emma Sophia Grace".split()
_EN_L = "Smith Johnson Williams Brown Jones Garcia Miller Davis Rodriguez Martinez Wilson Anderson Taylor Thomas Moore Jackson Martin Lee Thompson White Harris Clark Lewis Walker Hall".split()
_EN_CITY = "New\u00a0York Los\u00a0Angeles Chicago Houston Phoenix Philadelphia San\u00a0Antonio San\u00a0Diego Dallas Austin Seattle Denver Boston Portland Miami Atlanta".split()
_EN_STREET = "Main Oak Pine Maple Cedar Elm Washington Lake Hill Park Sunset Lincoln River Spring Highland".split()
_EN_CO = "Acme Globex Initech Umbrella Stark Wayne Hooli Vandelay Soylent Cyberdyne Wonka Tyrell Aperture Massive Dynamic".split()
_EN_JOB = "Manager Accountant Engineer Developer Designer Analyst Lawyer Marketer Doctor Teacher Logistician Architect Consultant".split()

_FIELDS = [("name", "ФИО", "Full name"), ("first", "Имя", "First name"), ("last", "Фамилия", "Last name"), ("gender", "Пол", "Gender"),
           ("email", "Email", "Email"), ("phone", "Телефон", "Phone"), ("birth", "Дата рождения", "Birth date"), ("age", "Возраст", "Age"),
           ("city", "Город", "City"), ("address", "Адрес", "Address"), ("zip", "Индекс", "ZIP"), ("company", "Компания", "Company"),
           ("job", "Должность", "Job"), ("inn", "ИНН (тест)", "INN (test)"), ("card", "Карта (тест, Луна)", "Card (test, Luhn)"),
           ("login", "Логин", "Username"), ("password", "Пароль", "Password"), ("uuid", "UUID", "UUID"), ("ip", "IP", "IP"),
           ("salary", "Зарплата", "Salary"), ("date", "Дата события", "Event date"), ("bool", "Да/нет", "Yes/no")]

def _luhn_card(prefix: str = "4") -> str:
    from .t_check import luhn_ok
    while True:
        body = prefix + "".join(str(_RND.randint(0, 9)) for _ in range(15 - len(prefix)))
        for d in range(10):
            if luhn_ok(body + str(d)):
                return body + str(d)

def fake_person(lang: str, fields: List[str]) -> Dict[str, Any]:
    from .t_text import translit
    ru = lang == "ru"
    female = _RND.random() < 0.5
    first = _RND.choice((_RU_F if female else _RU_M) if ru else (_EN_F if female else _EN_M))
    last = _RND.choice(_RU_L if ru else _EN_L)
    if ru and female:
        last = last + "а" if last.endswith(("ов", "ев", "ёв", "ин")) else last
    middle = ""
    if ru:
        middle = _RND.choice(_RU_P)
        if female:
            middle = middle.replace("ич", "на")
    birth = date.today() - timedelta(days=_RND.randint(18 * 365, 70 * 365))
    login = (translit(first, "simple")[:1] + translit(last, "simple")).lower() if ru else (first[:1] + last).lower()
    login = re.sub(r"[^a-z0-9]", "", login) + str(_RND.randint(1, 99))
    city = _RND.choice(_RU_CITY if ru else _EN_CITY).replace("\u00a0", " ")
    row = {
        "name": f"{last} {first} {middle}".strip() if ru else f"{first} {last}", "first": first, "last": last,
        "gender": ("ж" if female else "м") if ru else ("F" if female else "M"),
        "email": f"{login}@{_RND.choice(['example.com', 'example.org', 'mail.test', 'test.ru' if ru else 'test.dev'])}",
        "phone": (f"+7 9{_RND.randint(10, 99)} {_RND.randint(100, 999)}-{_RND.randint(10, 99)}-{_RND.randint(10, 99)}" if ru else
                  f"+1 ({_RND.randint(201, 989)}) 555-{_RND.randint(1000, 9999)}"),
        "birth": birth.isoformat(), "age": (date.today() - birth).days // 365, "city": city,
        "address": (f"г. {city}, ул. {_RND.choice(_RU_STREET)}, д. {_RND.randint(1, 150)}, кв. {_RND.randint(1, 300)}" if ru else
                    f"{_RND.randint(1, 9999)} {_RND.choice(_EN_STREET)} St, {city}"),
        "zip": f"{_RND.randint(101000, 692999)}" if ru else f"{_RND.randint(10000, 99999)}",
        "company": (f"ООО «{_RND.choice(_RU_CO)}»" if ru else f"{_RND.choice(_EN_CO)} {_RND.choice(['Inc.', 'LLC', 'Corp.', 'Ltd.'])}"),
        "job": _RND.choice(_RU_JOB if ru else _EN_JOB), "login": login,
        "password": "".join(_RND.choice(string.ascii_letters + string.digits) for _ in range(12)), "uuid": str(_uuid.uuid4()),
        "ip": f"{_RND.choice([10, 172, 192])}.{_RND.randint(0, 255)}.{_RND.randint(0, 255)}.{_RND.randint(1, 254)}",
        "salary": _RND.randrange(40000, 400000, 500) if ru else _RND.randrange(30000, 250000, 500),
        "date": (date.today() - timedelta(days=_RND.randint(0, 730))).isoformat(), "bool": _RND.random() < 0.5,
    }
    if "inn" in fields:
        from .t_check import gen_inn
        row["inn"] = gen_inn(12)
    if "card" in fields:
        row["card"] = _luhn_card(_RND.choice(["4", "51", "2200"]))
    return {k: row[k] for k in fields if k in row}

@utility("gen.fake", "gen", "Тестовые данные (люди, адреса, компании)", "Fake test data (people, addresses, companies)",
         "Правдоподобные вымышленные записи: ФИО, email, телефоны, адреса, компании, ИНН, карты (Луна) — в JSON, CSV, SQL или таблицей.",
         "Realistic made-up records: names, emails, phones, addresses, companies, card numbers (Luhn) — as JSON, CSV, SQL or a table.",
         input="none", opts=[O("lang", "choice", "Язык данных", "Data language", "ru", choices=[("ru", "Русские", "Russian"), ("en", "Английские", "English")]),
                             O("count", "int", "Записей", "Records", 10, min=1, max=10000),
                             O("fields", "text", "Поля через запятую", "Fields (comma-separated)", "name,email,phone,city,birth,company",
                               hint="Доступны: " + ", ".join(f[0] for f in _FIELDS), hint_en="Available: " + ", ".join(f[0] for f in _FIELDS)),
                             O("format", "choice", "Формат", "Format", "table", choices=[("table", "Таблица", "Table"), ("json", "JSON", "JSON"), ("csv", "CSV", "CSV"),
                                                                                  ("sql", "SQL INSERT", "SQL INSERT"), ("jsonl", "JSON Lines", "JSON Lines")])],
         ai_default=True, keys="тестовые данные фейк fake data mock люди адреса генератор")
def gen_fake(ctx):
    known = [f[0] for f in _FIELDS]
    fields = [f.strip() for f in str(ctx.o["fields"]).split(",") if f.strip() in known] or ["name", "email", "phone"]
    rows = [fake_person(ctx.o["lang"], fields) for _ in range(int(ctx.o["count"]))]
    fmt = ctx.o["format"]
    if fmt == "json":
        text = json.dumps(rows, ensure_ascii=False, indent=2)
        ctx.out_text("fake-data.json", text)
        return {"text": text, "lang": "json"}
    if fmt == "jsonl":
        text = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
        ctx.out_text("fake-data.jsonl", text)
        return {"text": text}
    if fmt == "csv":
        import csv
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
        ctx.out_bytes("fake-data.csv", ("\ufeff" + buf.getvalue()).encode("utf-8"))
        return {"text": buf.getvalue()}
    if fmt == "sql":
        def lit(v):
            if isinstance(v, bool):
                return "TRUE" if v else "FALSE"
            if isinstance(v, (int, float)):
                return str(v)
            return "'" + str(v).replace("'", "''") + "'"
        text = "\n".join(f"INSERT INTO people ({', '.join(fields)}) VALUES ({', '.join(lit(r[f]) for f in fields)});" for r in rows)
        ctx.out_text("fake-data.sql", text)
        return {"text": text, "lang": "sql"}
    names = {f[0]: (f[2] if ctx.en else f[1]) for f in _FIELDS}
    return {"table": table([names[f] for f in fields], [[r[f] for f in fields] for r in rows])}

# ── images: placeholder / identicon ─────────────────────────────────────────────────────────────────────────────────
def font(size: int, bold: bool = False):
    from PIL import ImageFont
    try:
        from ..memunote.formats.pdftools import unicode_font_path
        path = unicode_font_path("bold" if bold else "")
        if path:
            return ImageFont.truetype(path, size)
    except Exception:  # noqa: BLE001
        pass
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()

def save_image(img, fmt: str, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    fmt = fmt.lower()
    if fmt in ("jpg", "jpeg"):
        if img.mode in ("RGBA", "LA", "P"):
            from PIL import Image
            bg = Image.new("RGB", img.size, (255, 255, 255))
            rgba = img.convert("RGBA")
            bg.paste(rgba, mask=rgba.split()[3])
            img = bg
        img.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
    elif fmt == "webp":
        img.save(buf, "WEBP", quality=quality, method=4)
    elif fmt == "gif":
        img.convert("P", palette=1, colors=256).save(buf, "GIF")
    elif fmt == "bmp":
        img.convert("RGB").save(buf, "BMP")
    elif fmt in ("tif", "tiff"):
        img.save(buf, "TIFF", compression="tiff_lzw")
    elif fmt == "ico":
        img.convert("RGBA").save(buf, "ICO", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256) if s <= max(img.size)] or [(16, 16)])
    elif fmt == "avif":
        img.save(buf, "AVIF", quality=quality)
    else:
        img.save(buf, "PNG", optimize=True)
    return buf.getvalue()

@utility("gen.placeholder", "gen", "Картинка-заглушка", "Placeholder image",
         "Изображение нужного размера с подписью (например, 1200×630) — PNG, JPG, WebP или SVG.",
         "An image of any size with a caption (e.g. 1200×630) — PNG, JPG, WebP or SVG.",
         input="none", opts=[O("width", "int", "Ширина", "Width", 1200, min=1, max=8000), O("height", "int", "Высота", "Height", 630, min=1, max=8000),
                             O("bg", "color", "Фон", "Background", "#cbd5e1"), O("fg", "color", "Текст", "Text color", "#334155"),
                             O("text", "text", "Подпись (пусто — размер)", "Caption (empty = size)", ""),
                             O("format", "choice", "Формат", "Format", "png", choices=[("png", "PNG", "PNG"), ("jpg", "JPG", "JPG"), ("webp", "WebP", "WebP"), ("svg", "SVG", "SVG")])],
         ai_default=True, keys="заглушка placeholder картинка изображение размер dummy image")
def gen_placeholder(ctx):
    w, h = int(ctx.o["width"]), int(ctx.o["height"])
    caption = ctx.o["text"] or f"{w} × {h}"
    bg, fg = parse_color(ctx.o["bg"], (203, 213, 225)), parse_color(ctx.o["fg"], (51, 65, 85))
    if ctx.o["format"] == "svg":
        from html import escape
        size = max(10, min(w, h) // 8)
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
               f'<rect width="100%" height="100%" fill="rgb{tuple(bg[:3])}"/>'
               f'<text x="50%" y="50%" fill="rgb{tuple(fg[:3])}" font-family="Arial, sans-serif" font-size="{size}" '
               f'text-anchor="middle" dominant-baseline="middle">{escape(caption)}</text></svg>')
        ctx.out_text(f"placeholder-{w}x{h}.svg", svg)
        return {"text": svg, "lang": "xml"}
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (w, h), tuple(bg[:3]))
    draw = ImageDraw.Draw(img)
    size = max(8, min(w // max(4, len(caption) // 2 + 2), h // 4))
    f = font(size, True)
    box = draw.textbbox((0, 0), caption, font=f)
    draw.text(((w - (box[2] - box[0])) / 2 - box[0], (h - (box[3] - box[1])) / 2 - box[1]), caption, fill=tuple(fg[:3]), font=f)
    ext = ctx.o["format"]
    ctx.out_bytes(f"placeholder-{w}x{h}.{ext}", save_image(img, ext))
    return {"note": f"{w}×{h}"}

@utility("gen.identicon", "gen", "Аватар-идентикон", "Identicon avatar",
         "Уникальная симметричная аватарка из любой строки (email, ник) — как на GitHub. PNG или SVG.",
         "A unique symmetric avatar from any string (email, nickname) — GitHub style. PNG or SVG.",
         opts=[O("size", "int", "Размер, px", "Size, px", 256, min=16, max=2048), O("format", "choice", "Формат", "Format", "png", choices=[("png", "PNG", "PNG"), ("svg", "SVG", "SVG")]),
               O("round", "bool", "Круглая", "Round", False)],
         placeholder="user@example.com", keys="аватар identicon аватарка avatar")
def gen_identicon(ctx):
    seed = ctx.need_text("Введите строку: email или ник", "Enter a string: email or nickname").strip()
    digest = hashlib.md5(seed.encode("utf-8")).digest()
    hue = digest[0] / 255 * 360
    from .colors import from_hsl, to_hex
    color = from_hsl(hue, 55 + digest[1] % 20, 45 + digest[2] % 15)
    cells = [[(digest[3 + (r * 3 + min(c, 4 - c)) % 13] & 1) == 1 for c in range(5)] for r in range(5)]
    size = int(ctx.o["size"])
    if ctx.o["format"] == "svg":
        cell = 100 / 6
        rects = "".join(f'<rect x="{cell * (c + 0.5):.2f}" y="{cell * (r + 0.5):.2f}" width="{cell:.2f}" height="{cell:.2f}"/>'
                        for r in range(5) for c in range(5) if cells[r][c])
        clip = '<clipPath id="c"><circle cx="50" cy="50" r="50"/></clipPath>' if ctx.o["round"] else ""
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 100 100">{clip}'
               f'<g{" clip-path=" + chr(34) + "url(#c)" + chr(34) if clip else ""}><rect width="100" height="100" fill="#f0f0f0"/><g fill="{to_hex(color)}">{rects}</g></g></svg>')
        ctx.out_text("identicon.svg", svg)
        return {"text": svg, "lang": "xml"}
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (240, 240, 240, 255))
    d = ImageDraw.Draw(img)
    cell = size / 6
    for r in range(5):
        for c in range(5):
            if cells[r][c]:
                d.rectangle([cell * (c + 0.5), cell * (r + 0.5), cell * (c + 1.5) - 1, cell * (r + 1.5) - 1], fill=tuple(color))
    if ctx.o["round"]:
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, size - 1, size - 1], fill=255)
        img.putalpha(mask)
    ctx.out_bytes("identicon.png", save_image(img, "png"))
    return ""

# ── QR codes ────────────────────────────────────────────────────────────────────────────────────────────────────────
def _qr_payload(ctx) -> str:
    kind, text = ctx.o["kind"], (ctx.text or "").strip()
    if kind == "text" or kind == "url":
        if not text:
            raise UtilError("Введите текст или ссылку", "Enter text or a link")
        return text
    if kind == "wifi":
        esc = lambda s: re.sub(r"([\\;,:\"])", r"\\\1", s)  # noqa: E731
        ssid = ctx.o["ssid"] or text
        if not ssid:
            raise UtilError("Укажите имя сети", "Enter the network name")
        auth = ctx.o["auth"]
        return f"WIFI:T:{auth};S:{esc(ssid)};P:{esc(ctx.o['wifi_pass'])};{'H:true;' if ctx.o['hidden'] else ''};" if auth != "nopass" else f"WIFI:T:nopass;S:{esc(ssid)};;"
    if kind == "vcard":
        rows = dict(line.split(":", 1) for line in lines_of(text) if ":" in line)
        lowered = {k.strip().lower(): v.strip() for k, v in rows.items()}
        name = lowered.get("name") or lowered.get("имя") or (lines_of(text)[0] if text else "")
        parts = ["BEGIN:VCARD", "VERSION:3.0", f"FN:{name}", f"N:{';'.join(reversed(name.split(' ', 1))) if ' ' in name else name};;;"]
        for keys, tag in ((("phone", "tel", "телефон"), "TEL;TYPE=CELL"), (("email", "почта"), "EMAIL"), (("org", "company", "компания"), "ORG"),
                          (("title", "должность"), "TITLE"), (("url", "site", "сайт"), "URL"), (("address", "адрес"), "ADR;TYPE=WORK:;;")):
            for k in keys:
                if lowered.get(k):
                    parts.append(f"{tag}:{lowered[k]}" if not tag.endswith(";;") else f"{tag}{lowered[k]};;;;")
                    break
        parts.append("END:VCARD")
        return "\n".join(parts)
    if kind == "email":
        addr, _, body = text.partition("\n")
        return f"mailto:{addr.strip()}" + (f"?body={__import__('urllib.parse').parse.quote(body.strip())}" if body.strip() else "")
    if kind == "sms":
        num, _, body = text.partition("\n")
        return f"SMSTO:{re.sub(r'[^0-9+]', '', num)}:{body.strip()}"
    if kind == "tel":
        return "tel:" + re.sub(r"[^0-9+]", "", text)
    if kind == "geo":
        m = re.findall(r"-?\d+(?:\.\d+)?", text)
        if len(m) < 2:
            raise UtilError("Укажите координаты: 55.7558, 37.6173", "Enter coordinates: 55.7558, 37.6173")
        return f"geo:{m[0]},{m[1]}"
    return text

@utility("gen.qr", "gen", "QR-код", "QR code",
         "QR для ссылки, текста, Wi-Fi (подключение одним наведением), визитки vCard, email, SMS, звонка или координат. PNG или SVG.",
         "QR for a link, text, Wi-Fi (join by pointing the camera), a vCard, email, SMS, a call or coordinates. PNG or SVG.",
         opts=[O("kind", "choice", "Содержимое", "Content", "url", choices=[("url", "Ссылка", "Link"), ("text", "Текст", "Text"), ("wifi", "Wi-Fi", "Wi-Fi"),
                                                                        ("vcard", "Визитка (vCard)", "Contact (vCard)"), ("email", "Email", "Email"), ("sms", "SMS", "SMS"),
                                                                        ("tel", "Звонок", "Phone call"), ("geo", "Координаты", "Location")]),
               O("ssid", "text", "Имя сети (SSID)", "Network name (SSID)", "", show="kind=wifi"),
               O("wifi_pass", "password", "Пароль Wi-Fi", "Wi-Fi password", "", show="kind=wifi", ai=False),
               O("auth", "choice", "Защита", "Security", "WPA", show="kind=wifi", choices=[("WPA", "WPA/WPA2/WPA3", "WPA/WPA2/WPA3"), ("WEP", "WEP", "WEP"), ("nopass", "Без пароля", "Open")]),
               O("hidden", "bool", "Скрытая сеть", "Hidden network", False, show="kind=wifi"),
               O("format", "choice", "Формат", "Format", "png", choices=[("png", "PNG", "PNG"), ("svg", "SVG", "SVG")]),
               O("size", "int", "Размер, px", "Size, px", 600, min=64, max=4000), O("fg", "color", "Цвет", "Color", "#000000"),
               O("bg", "color", "Фон", "Background", "#ffffff"), O("ecc", "choice", "Коррекция ошибок", "Error correction", "M",
                                                                 choices=[("L", "L — 7%", "L — 7%"), ("M", "M — 15%", "M — 15%"), ("Q", "Q — 25%", "Q — 25%"), ("H", "H — 30% (для логотипа)", "H — 30% (for a logo)")])],
         ai_default=True, placeholder="https://example.com", keys="qr код qr-код wifi визитка vcard ссылка qrcode")
def gen_qr(ctx):
    try:
        import qrcode
        import qrcode.constants
    except ImportError:
        raise UtilError("Библиотека qrcode не установлена", "The qrcode library is missing")
    payload = _qr_payload(ctx)
    ecc = {"L": qrcode.constants.ERROR_CORRECT_L, "M": qrcode.constants.ERROR_CORRECT_M, "Q": qrcode.constants.ERROR_CORRECT_Q,
           "H": qrcode.constants.ERROR_CORRECT_H}[ctx.o["ecc"]]
    qr = qrcode.QRCode(error_correction=ecc, border=4, box_size=10)
    qr.add_data(payload)
    try:
        qr.make(fit=True)
    except Exception as exc:  # noqa: BLE001 - data too long for any version
        raise UtilError(f"Слишком много данных для QR-кода: {exc}", f"Too much data for a QR code: {exc}")
    matrix = qr.get_matrix()
    n = len(matrix)
    fg, bg = parse_color(ctx.o["fg"], (0, 0, 0)), parse_color(ctx.o["bg"], (255, 255, 255))
    from .colors import to_hex
    if ctx.o["format"] == "svg":
        path = "".join(f"M{c},{r}h1v1h-1z" for r, row in enumerate(matrix) for c, v in enumerate(row) if v)
        size = int(ctx.o["size"])
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {n} {n}" shape-rendering="crispEdges">'
               f'<rect width="{n}" height="{n}" fill="{to_hex(bg)}"/><path fill="{to_hex(fg)}" d="{path}"/></svg>')
        ctx.out_text("qr.svg", svg)
    else:
        from PIL import Image
        scale = max(1, int(ctx.o["size"]) // n)
        img = Image.new("RGB", (n, n), tuple(bg[:3]))
        px = img.load()
        for r, row in enumerate(matrix):
            for c, v in enumerate(row):
                if v:
                    px[c, r] = tuple(fg[:3])
        img = img.resize((n * scale, n * scale), Image.NEAREST)
        ctx.out_bytes("qr.png", save_image(img, "png"))
    return {"note": ctx.tr(f"Версия QR {qr.version}, {n}×{n} модулей · закодировано: {payload[:80]}", f"QR version {qr.version}, {n}×{n} modules · encoded: {payload[:80]}")}

# ── barcodes ────────────────────────────────────────────────────────────────────────────────────────────────────────
_C128 = ("212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 221312 231212 112232 122132 122231 113222 123122 123221 "
         "223211 221132 221231 213212 223112 312131 311222 321122 321221 312212 322112 322211 212123 212321 232121 111323 131123 131321 "
         "112313 132113 132311 211313 231113 231311 112133 112331 132131 113123 113321 133121 313121 211331 231131 213113 213311 213131 "
         "311123 311321 331121 312113 312311 332111 314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 112412 122114 "
         "122411 142112 142211 241211 221114 413111 241112 134111 111242 121142 121241 114212 124112 124211 411212 421112 421211 212141 "
         "214121 412121 111143 111341 131141 114113 114311 411113 411311 113141 114131 311141 411131 211412 211214 211232 2331112").split()

def code128_widths(data: str) -> List[int]:
    if not data or any(ord(ch) > 126 or (ord(ch) < 32) for ch in data):
        raise UtilError("Code 128 кодирует только латиницу, цифры и знаки ASCII", "Code 128 encodes ASCII letters, digits and symbols only")
    codes: List[int] = []
    i, mode = 0, None
    def digits_run(pos):
        n = 0
        while pos + n < len(data) and data[pos + n].isdigit():
            n += 1
        return n
    while i < len(data):
        run = digits_run(i)
        if run >= 4 or (run >= 2 and run == len(data) - i and mode == "C"):
            take = run - run % 2
            if mode != "C":
                codes.append(105 if mode is None else 99)
                mode = "C"
            for k in range(i, i + take, 2):
                codes.append(int(data[k:k + 2]))
            i += take
            continue
        if mode != "B":
            codes.append(104 if mode is None else 100)
            mode = "B"
        codes.append(ord(data[i]) - 32)
        i += 1
    check = (codes[0] + sum(c * k for k, c in enumerate(codes[1:], 1))) % 103
    codes += [check, 106]
    return [int(w) for c in codes for w in _C128[c]]

_EAN_L = "0001101 0011001 0010011 0111101 0100011 0110001 0101111 0111011 0110111 0001011".split()
_EAN_G = ["".join(reversed("".join("1" if b == "0" else "0" for b in l))) for l in _EAN_L]
_EAN_R = ["".join("1" if b == "0" else "0" for b in l) for l in _EAN_L]
_EAN_PAR = "LLLLLL LLGLGG LLGGLG LLGGGL LGLLGG LGGLLG LGGGLL LGLGLG LGLGGL LGGLGL".split()

def ean_bits(code: str) -> str:
    from .t_check import ean_check_digit
    digits = re.sub(r"\D", "", code)
    if len(digits) in (7, 12):
        digits += str(ean_check_digit(digits))
    if len(digits) == 13:
        if ean_check_digit(digits[:12]) != int(digits[12]):
            raise UtilError("Неверная контрольная цифра EAN-13", "Wrong EAN-13 check digit")
        par = _EAN_PAR[int(digits[0])]
        left = "".join((_EAN_L if p == "L" else _EAN_G)[int(d)] for p, d in zip(par, digits[1:7]))
        right = "".join(_EAN_R[int(d)] for d in digits[7:])
        return "101" + left + "01010" + right + "101", digits
    if len(digits) == 8:
        if ean_check_digit(digits[:7]) != int(digits[7]):
            raise UtilError("Неверная контрольная цифра EAN-8", "Wrong EAN-8 check digit")
        return "101" + "".join(_EAN_L[int(d)] for d in digits[:4]) + "01010" + "".join(_EAN_R[int(d)] for d in digits[4:]) + "101", digits
    raise UtilError("Для EAN нужно 7/8 или 12/13 цифр", "EAN needs 7/8 or 12/13 digits")

_C39 = {"0": "nnnwwnwnn", "1": "wnnwnnnnw", "2": "nnwwnnnnw", "3": "wnwwnnnnn", "4": "nnnwwnnnw", "5": "wnnwwnnnn", "6": "nnwwwnnnn",
        "7": "nnnwnnwnw", "8": "wnnwnnwnn", "9": "nnwwnnwnn", "A": "wnnnnwnnw", "B": "nnwnnwnnw", "C": "wnwnnwnnn", "D": "nnnnwwnnw",
        "E": "wnnnwwnnn", "F": "nnwnwwnnn", "G": "nnnnnwwnw", "H": "wnnnnwwnn", "I": "nnwnnwwnn", "J": "nnnnwwwnn", "K": "wnnnnnnww",
        "L": "nnwnnnnww", "M": "wnwnnnnwn", "N": "nnnnwnnww", "O": "wnnnwnnwn", "P": "nnwnwnnwn", "Q": "nnnnnnwww", "R": "wnnnnnwwn",
        "S": "nnwnnnwwn", "T": "nnnnwnwwn", "U": "wwnnnnnnw", "V": "nwwnnnnnw", "W": "wwwnnnnnn", "X": "nwnnwnnnw", "Y": "wwnnwnnnn",
        "Z": "nwwnwnnnn", "-": "nwnnnnwnw", ".": "wwnnnnwnn", " ": "nwwnnnwnn", "*": "nwnnwnwnn", "$": "nwnwnwnnn", "/": "nwnwnnnwn",
        "+": "nwnnnwnwn", "%": "nnnwnwnwn"}

def code39_widths(data: str) -> List[int]:
    data = data.upper()
    bad = [ch for ch in data if ch not in _C39 or ch == "*"]
    if bad:
        raise UtilError(f"Code 39 не кодирует символы: {''.join(bad)}", f"Code 39 cannot encode: {''.join(bad)}")
    out: List[int] = []
    for ch in "*" + data + "*":
        out += [3 if w == "w" else 1 for w in _C39[ch]] + [1]
    return out[:-1]

def _bars_from_widths(widths: List[int]) -> str:
    return "".join(("1" if i % 2 == 0 else "0") * w for i, w in enumerate(widths))

@utility("gen.barcode", "gen", "Штрихкод (Code 128, EAN-13, EAN-8, Code 39)", "Barcode (Code 128, EAN-13, EAN-8, Code 39)",
         "Штрихкоды для товаров, складов и этикеток в PNG или SVG; контрольная цифра EAN считается сама.",
         "Barcodes for products, warehouses and labels as PNG or SVG; the EAN check digit is computed for you.",
         opts=[O("type", "choice", "Тип", "Type", "code128", choices=[("code128", "Code 128", "Code 128"), ("ean13", "EAN-13", "EAN-13"), ("ean8", "EAN-8", "EAN-8"), ("code39", "Code 39", "Code 39")]),
               O("format", "choice", "Формат", "Format", "png", choices=[("png", "PNG", "PNG"), ("svg", "SVG", "SVG")]),
               O("height", "int", "Высота, px", "Height, px", 120, min=20, max=1000), O("module", "int", "Ширина модуля, px", "Module width, px", 3, min=1, max=12),
               O("label", "bool", "Подпись под кодом", "Text under the code", True)],
         ai_default=True, placeholder="4600000000005", keys="штрихкод barcode ean code128 этикетка")
def gen_barcode(ctx):
    data = ctx.need_text("Введите данные для штрихкода", "Enter the data to encode").strip()
    kind = ctx.o["type"]
    label = data
    if kind == "code128":
        bits = _bars_from_widths(code128_widths(data))
    elif kind == "code39":
        bits = _bars_from_widths(code39_widths(data))
    else:
        bits, label = ean_bits(data)
        if (kind == "ean13") != (len(label) == 13):
            raise UtilError("Число цифр не подходит к выбранному типу EAN", "The digit count does not fit the chosen EAN type")
    m, h = int(ctx.o["module"]), int(ctx.o["height"])
    quiet = 10 * m
    width = len(bits) * m + quiet * 2
    text_h = (h // 5 + 8) if ctx.o["label"] else 0
    if ctx.o["format"] == "svg":
        from html import escape
        rects, x = [], quiet
        i = 0
        while i < len(bits):
            if bits[i] == "1":
                j = i
                while j < len(bits) and bits[j] == "1":
                    j += 1
                rects.append(f'<rect x="{quiet + i * m}" y="10" width="{(j - i) * m}" height="{h}"/>')
                i = j
            else:
                i += 1
        txt = (f'<text x="{width / 2}" y="{h + 10 + text_h - 6}" font-family="monospace" font-size="{h // 5}" text-anchor="middle">{escape(label)}</text>'
               if ctx.o["label"] else "")
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{h + 20 + text_h}" viewBox="0 0 {width} {h + 20 + text_h}">'
               f'<rect width="100%" height="100%" fill="#fff"/><g fill="#000">{"".join(rects)}</g>{txt}</svg>')
        ctx.out_text(f"barcode-{re.sub(r'[^A-Za-z0-9]', '_', label)[:40]}.svg", svg)
        return {"note": label}
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (width, h + 20 + text_h), (255, 255, 255))
    d = ImageDraw.Draw(img)
    for i, b in enumerate(bits):
        if b == "1":
            d.rectangle([quiet + i * m, 10, quiet + (i + 1) * m - 1, 10 + h], fill=(0, 0, 0))
    if ctx.o["label"]:
        f = font(max(10, h // 5))
        box = d.textbbox((0, 0), label, font=f)
        d.text(((width - (box[2] - box[0])) / 2, h + 14), label, fill=(0, 0, 0), font=f)
    ctx.out_bytes(f"barcode-{re.sub(r'[^A-Za-z0-9]', '_', label)[:40]}.png", save_image(img, "png"))
    return {"note": label}

# ── random ──────────────────────────────────────────────────────────────────────────────────────────────────────────
@utility("gen.random", "gen", "Случайные числа, жребий, команды", "Random numbers, draws, teams",
         "Случайные числа в диапазоне (без повторов), кубики, монетка, выбор победителя из списка, деление на команды.",
         "Random numbers in a range (unique), dice, coin flips, picking winners from a list, splitting into teams.",
         opts=[O("mode", "choice", "Что сделать", "Mode", "numbers", choices=[("numbers", "Числа в диапазоне", "Numbers in a range"), ("dice", "Бросить кубики", "Roll dice"),
                                                                       ("coin", "Подбросить монетку", "Flip coins"), ("pick", "Выбрать из списка", "Pick from the list"),
                                                                       ("teams", "Разделить на команды", "Split into teams"), ("shuffle", "Перемешать список", "Shuffle the list")]),
               O("min", "int", "От", "From", 1, show="mode=numbers"), O("max", "int", "До", "To", 100, show="mode=numbers"),
               O("count", "int", "Сколько (чисел, кубиков, победителей, команд)", "How many (numbers, dice, winners, teams)", 1, min=1, max=10000),
               O("unique", "bool", "Без повторов", "Unique", True, show="mode=numbers|pick"),
               O("sides", "int", "Граней у кубика", "Dice sides", 6, min=2, max=1000, show="mode=dice")],
         live=False, ai_default=True, placeholder="Список для выбора/команд — по строке", placeholder_en="List to pick from — one item per line",
         keys="случайное число рандом жребий кубик монетка команды random dice coin pick winner")
def gen_random(ctx):
    mode, n = ctx.o["mode"], int(ctx.o["count"])
    if mode == "numbers":
        lo, hi = sorted((int(ctx.o["min"]), int(ctx.o["max"])))
        if ctx.o["unique"]:
            if n > hi - lo + 1:
                raise UtilError("Уникальных чисел в диапазоне меньше, чем нужно", "The range has fewer unique numbers than requested")
            nums = _RND.sample(range(lo, hi + 1), n)
        else:
            nums = [_RND.randint(lo, hi) for _ in range(n)]
        return ", ".join(map(str, nums))
    if mode == "dice":
        rolls = [_RND.randint(1, int(ctx.o["sides"])) for _ in range(min(n, 1000))]
        faces = "⚀⚁⚂⚃⚄⚅"
        shown = " ".join(faces[r - 1] if int(ctx.o["sides"]) == 6 else str(r) for r in rolls)
        return {"text": shown, "note": ctx.tr(f"Сумма: {sum(rolls)}", f"Total: {sum(rolls)}")}
    if mode == "coin":
        flips = [_RND.choice([ctx.tr("орёл", "heads"), ctx.tr("решка", "tails")]) for _ in range(min(n, 10000))]
        heads = flips.count(ctx.tr("орёл", "heads"))
        return {"text": ", ".join(flips[:200]) + (" …" if len(flips) > 200 else ""), "note": ctx.tr(f"Орёл: {heads}, решка: {len(flips) - heads}", f"Heads: {heads}, tails: {len(flips) - heads}")}
    items = [x.strip() for x in lines_of(ctx.need_text("Введите список, по элементу в строке", "Enter a list, one item per line")) if x.strip()]
    if mode == "shuffle":
        _RND.shuffle(items)
        return "\n".join(items)
    if mode == "pick":
        if ctx.o["unique"]:
            return "\n".join(_RND.sample(items, min(n, len(items))))
        return "\n".join(_RND.choice(items) for _ in range(n))
    _RND.shuffle(items)
    teams = [items[i::n] for i in range(min(n, len(items)))]
    return "\n\n".join(ctx.tr(f"Команда {i}", f"Team {i}") + ":\n" + "\n".join("• " + x for x in t) for i, t in enumerate(teams, 1))

# ── palette / gradient ──────────────────────────────────────────────────────────────────────────────────────────────
@utility("gen.palette", "gen", "Цветовая палитра", "Color palette",
         "Гармоничная палитра от базового цвета: комплементарная, аналоговая, триада, тетрада, монохром, оттенки — с CSS-переменными.",
         "A harmonious palette from a base color: complementary, analogous, triad, tetrad, monochrome, shades — with CSS variables.",
         opts=[O("scheme", "choice", "Схема", "Scheme", "analogous", choices=[("analogous", "Аналоговая", "Analogous"), ("complementary", "Комплементарная", "Complementary"),
                                                                        ("triad", "Триада", "Triad"), ("tetrad", "Тетрада", "Tetrad"), ("split", "Раздельно-комплементарная", "Split complementary"),
                                                                        ("mono", "Монохромная", "Monochrome"), ("shades", "Шкала 50–900 (как Tailwind)", "50–900 scale (Tailwind-like)"),
                                                                        ("random", "Случайная приятная", "Random pleasant")])],
         live=True, ai_default=True, placeholder="#3b82f6", keys="палитра цвета гармония palette colors scheme tailwind")
def gen_palette(ctx):
    from .colors import from_hsl, to_hex, to_hsl
    scheme = ctx.o["scheme"]
    if scheme == "random" or not (ctx.text or "").strip():
        base = from_hsl(_RND.randint(0, 359), _RND.randint(55, 85), _RND.randint(45, 60))
    else:
        base = parse_color(ctx.text, None) if ctx.text else (59, 130, 246)
    h, s, l = to_hsl(base)
    if scheme == "analogous":
        cols = [from_hsl((h + d) % 360, s, l) for d in (-40, -20, 0, 20, 40)]
    elif scheme == "complementary":
        cols = [from_hsl(h, s, max(15, l - 20)), base, from_hsl(h, s, min(90, l + 20)), from_hsl((h + 180) % 360, s, l), from_hsl((h + 180) % 360, s, min(90, l + 20))]
    elif scheme == "triad":
        cols = [base, from_hsl((h + 120) % 360, s, l), from_hsl((h + 240) % 360, s, l)]
    elif scheme == "tetrad":
        cols = [from_hsl((h + d) % 360, s, l) for d in (0, 90, 180, 270)]
    elif scheme == "split":
        cols = [base, from_hsl((h + 150) % 360, s, l), from_hsl((h + 210) % 360, s, l)]
    elif scheme == "mono":
        cols = [from_hsl(h, s, v) for v in (20, 35, 50, 65, 80)]
    elif scheme == "shades":
        cols = [from_hsl(h, min(100, s + (5 if v > 50 else 0)), v) for v in (97, 93, 86, 76, 64, 52, 43, 35, 27, 19)]
    else:
        cols = [from_hsl((h + d) % 360, max(30, s - _RND.randint(0, 20)), max(20, min(85, l + _RND.randint(-20, 20)))) for d in (0, 30, 60, 180, 210)]
    hexes = [to_hex(c) for c in cols]
    names = ["50", "100", "200", "300", "400", "500", "600", "700", "800", "900"] if scheme == "shades" else [str(i + 1) for i in range(len(hexes))]
    css = ":root {\n" + "\n".join(f"  --color-{n}: {x};" for n, x in zip(names, hexes)) + "\n}"
    from .t_check import _swatch_png
    ctx.out_bytes("palette.png", _swatch_png(cols, 120))
    return {"text": css, "lang": "css", "swatches": hexes}

@utility("gen.gradient", "gen", "Градиент (CSS + картинка)", "Gradient (CSS + image)",
         "Линейный, радиальный или конический градиент из 2–5 цветов: готовый CSS и PNG нужного размера.",
         "Linear, radial or conic gradient from 2–5 colors: ready CSS and a PNG of any size.",
         opts=[O("kind", "choice", "Вид", "Kind", "linear", choices=[("linear", "Линейный", "Linear"), ("radial", "Радиальный", "Radial"), ("conic", "Конический", "Conic")]),
               O("angle", "int", "Угол, °", "Angle, °", 135, min=0, max=360, show="kind=linear|conic"),
               O("width", "int", "Ширина PNG", "PNG width", 1200, min=8, max=6000), O("height", "int", "Высота PNG", "PNG height", 630, min=8, max=6000)],
         live=False, placeholder="#ff7e5f, #feb47b", keys="градиент gradient css фон")
def gen_gradient(ctx):
    raw = [c for c in re.split(r"[\s,;]+(?![^()]*\))", (ctx.text or "#6366f1, #ec4899").strip()) if c]
    cols = [parse_color(c, None) for c in raw][:5]
    if len(cols) < 2:
        raise UtilError("Нужно хотя бы два цвета", "Need at least two colors")
    from .colors import to_hex
    hexes = [to_hex(c) for c in cols]
    kind, angle = ctx.o["kind"], int(ctx.o["angle"])
    css = {"linear": f"linear-gradient({angle}deg, {', '.join(hexes)})", "radial": f"radial-gradient(circle, {', '.join(hexes)})",
           "conic": f"conic-gradient(from {angle}deg, {', '.join(hexes)})"}[kind]
    from PIL import Image
    w, h = int(ctx.o["width"]), int(ctx.o["height"])
    small_w, small_h = max(2, min(w, 600)), max(2, min(h, 600))
    img = Image.new("RGB", (small_w, small_h))
    px = img.load()
    rad = math.radians(angle)
    dx, dy = math.sin(rad), -math.cos(rad)
    half = abs(small_w * dx) / 2 + abs(small_h * dy) / 2 or 1
    for y in range(small_h):
        for x in range(small_w):
            cx, cy = x - small_w / 2, y - small_h / 2
            if kind == "linear":
                t = (cx * dx + cy * dy) / (2 * half) + 0.5
            elif kind == "radial":
                t = math.hypot(cx / (small_w / 2), cy / (small_h / 2)) / math.sqrt(2)
            else:
                t = ((math.degrees(math.atan2(cx, -cy)) - angle) % 360) / 360
            t = min(1.0, max(0.0, t)) * (len(cols) - 1)
            i = min(int(t), len(cols) - 2)
            f = t - i
            a, b = cols[i], cols[i + 1]
            px[x, y] = tuple(int(a[k] + (b[k] - a[k]) * f) for k in range(3))
    img = img.resize((w, h), Image.BICUBIC)
    ctx.out_bytes(f"gradient-{w}x{h}.png", save_image(img, "png"))
    return {"text": f"background: {css};", "lang": "css", "swatches": hexes}

# ── dummy files ─────────────────────────────────────────────────────────────────────────────────────────────────────
def _pad_bytes(n: int) -> bytes:
    """Incompressible filler (random letters: safe inside text-like containers)."""
    raw = os.urandom(max(0, n))
    return bytes(65 + b % 26 + (b & 32) for b in raw) if n < (8 << 20) else base64.b64encode(raw)[:n]

def _pad_png(data: bytes, target: int) -> bytes:
    import zlib
    need = target - len(data) - 12
    if need <= 0:
        return data
    chunk = b"muPd" + _pad_bytes(need)  # a private ancillary chunk: every decoder skips it
    block = struct.pack(">I", need) + chunk + struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)
    return data[:-12] + block + data[-12:]

def _pad_jpg(data: bytes, target: int) -> bytes:
    need, segs = target - len(data), []
    while need > 4:
        part = min(need - 4, 65533)
        segs.append(b"\xff\xfe" + struct.pack(">H", part + 2) + _pad_bytes(part))  # COM segments
        need -= part + 4
    return data[:2] + b"".join(segs) + data[2:]

def _pad_pdf(data: bytes, target: int) -> bytes:
    need = target - len(data) - 600
    if need <= 0:
        return data
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    writer.add_attachment("padding.bin", os.urandom(need))
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()

def _pad_ooxml(data: bytes, target: int) -> bytes:
    """DOCX/XLSX: grows the package with a real (padded) docProps/thumbnail.jpeg, as Office itself stores one."""
    import zipfile
    need = target - len(data) - 2000
    if need <= 0:
        return data
    from PIL import Image
    thumb = save_image(Image.new("RGB", (256, 192), (226, 232, 240)), "jpg", 80)
    thumb = _pad_jpg(thumb, need)
    src = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for info in src.infolist():
            body = src.read(info.filename)
            if info.filename == "[Content_Types].xml" and b'Extension="jpeg"' not in body:
                body = body.replace(b"<Default ", b'<Default Extension="jpeg" ContentType="image/jpeg"/><Default ', 1)
            elif info.filename == "_rels/.rels" and b"thumbnail" not in body:
                body = body.replace(b"</Relationships>", b'<Relationship Id="rIdThumb" Type="http://schemas.openxmlformats.org/package/2006/'
                                                          b'relationships/metadata/thumbnail" Target="docProps/thumbnail.jpeg"/></Relationships>')
            out.writestr(info.filename, body)
        out.writestr(zipfile.ZipInfo("docProps/thumbnail.jpeg", time.localtime()[:6]), thumb, compress_type=zipfile.ZIP_STORED)
    return buf.getvalue()

@utility("gen.dummy", "gen", "Файл-заглушка нужного типа и размера", "Dummy file of any type and size",
         "Настоящие тестовые файлы: TXT, JSON, CSV, PNG, JPG, PDF, DOCX, XLSX, ZIP, WAV или «пустышка» заданного размера — для проверки загрузок и лимитов.",
         "Real test files: TXT, JSON, CSV, PNG, JPG, PDF, DOCX, XLSX, ZIP, WAV or a blob of an exact size — for testing uploads and limits.",
         input="none", opts=[O("type", "choice", "Тип", "Type", "bin", choices=[("bin", "Двоичный (случайные байты)", "Binary (random bytes)"), ("zero", "Нули", "Zeros"),
                                                                     ("txt", "TXT (текст-рыба)", "TXT (lorem)"), ("json", "JSON (записи)", "JSON (records)"), ("csv", "CSV (записи)", "CSV (records)"),
                                                                     ("png", "PNG", "PNG"), ("jpg", "JPG", "JPG"), ("pdf", "PDF", "PDF"), ("docx", "DOCX", "DOCX"),
                                                                     ("xlsx", "XLSX", "XLSX"), ("zip", "ZIP", "ZIP"), ("wav", "WAV (тишина/тон)", "WAV (silence/tone)")]),
                             O("size", "number", "Размер, МБ", "Size, MB", 1, min=0.001, max=2048),
                             O("name", "text", "Имя файла", "File name", "dummy")],
         ai_default=True, keys="заглушка тестовый файл размер dummy file test upload пустой")
def gen_dummy(ctx):
    kind, name = ctx.o["type"], re.sub(r'[\\/:*?"<>|]+', "_", (ctx.o["name"] or "dummy").strip()) or "dummy"
    size = max(1, int(float(ctx.o["size"]) * 1024 * 1024))
    path = ctx.out_path(f"{name}.{kind if kind not in ('zero',) else 'bin'}")
    if kind in ("bin", "zero"):
        with open(path, "wb") as fh:
            left = size
            while left > 0:
                chunk = min(left, 1 << 20)
                fh.write(os.urandom(chunk) if kind == "bin" else bytes(chunk))
                left -= chunk
    elif kind == "txt":
        words = _LOREM
        with open(path, "w", encoding="utf-8") as fh:
            written = 0
            while written < size:
                para = " ".join(lorem_sentence(words) for _ in range(6)) + "\n\n"
                fh.write(para)
                written += len(para.encode("utf-8"))
    elif kind in ("json", "csv"):
        import csv
        fields = ["name", "email", "phone", "city", "company", "birth"]
        with open(path, "w", newline="", encoding="utf-8") as fh:
            if kind == "csv":
                head = io.StringIO()
                csv.writer(head).writerow(fields)
                fh.write(head.getvalue())
                written = len(head.getvalue())
            else:
                fh.write("[\n")
                written = 2
            first = True
            while written < size - (2 if kind == "json" else 0):
                rec = fake_person("en", fields)
                if kind == "csv":
                    line = io.StringIO()
                    csv.writer(line).writerow(list(rec.values()))
                    chunk = line.getvalue()
                else:
                    chunk = ("" if first else ",\n") + json.dumps(rec, ensure_ascii=False)
                fh.write(chunk)
                written += len(chunk.encode("utf-8"))
                first = False
            if kind == "json":
                fh.write("\n]\n")
    elif kind in ("png", "jpg"):
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (800, 600), (226, 232, 240))
        d = ImageDraw.Draw(img)
        d.text((40, 280), f"{name}.{kind} — 800×600", fill=(51, 65, 85), font=font(40, True))
        data = save_image(img, kind)
        path.write_bytes(_pad_png(data, size) if kind == "png" else _pad_jpg(data, size))
    elif kind == "pdf":
        path.write_bytes(_pad_pdf(simple_pdf([f"{name}.pdf", "Lorem ipsum dolor sit amet."]), size))
    elif kind == "docx":
        from ..memunote.formats import docx as docx_mod, richhtml
        data = docx_mod.write_docx(richhtml.text_to_blocks(f"{name}\n\n" + "\n\n".join(lorem_sentence(_LOREM) for _ in range(5))), name)
        path.write_bytes(_pad_ooxml(data, size))
    elif kind == "xlsx":
        from .t_data import write_xlsx
        rows = [["#", "Name", "Email", "City"]] + [[i, *fake_person("en", ["name", "email", "city"]).values()] for i in range(1, 51)]
        path.write_bytes(_pad_ooxml(write_xlsx({"Sheet1": rows}), size))
    elif kind == "zip":
        import zipfile
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("readme.txt", "Dummy archive\n")
            z.writestr("data/records.json", json.dumps([fake_person("en", ["name", "email"]) for _ in range(20)], indent=1))
            left = size - 1200
            if left > 0:
                z.writestr(zipfile.ZipInfo("data/random.bin", time.localtime()[:6]), os.urandom(left), compress_type=zipfile.ZIP_STORED)
    elif kind == "wav":
        import wave
        rate = 44100
        second = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(rate))
        frames = max(rate // 10, (size - 44) // 2)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            left = frames * 2
            while left > 0:
                part = second[:min(left, len(second))]
                w.writeframes(part)
                left -= len(part)
    from .core import human_size
    return {"note": ctx.tr(f"Создан {path.name}: {human_size(path.stat().st_size)}", f"Created {path.name}: {human_size(path.stat().st_size, True)}")}

def simple_pdf(lines: List[str]) -> bytes:
    """A one-page PDF with Latin text lines (Helvetica) — enough for a test file."""
    content = "BT /F1 18 Tf 72 760 Td 24 TL " + " ".join(f"({l.encode('latin-1', 'replace').decode('latin-1').replace(chr(92), '').replace('(', '[').replace(')', ']')}) '" for l in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", f"<< /Length {len(content)} >>\nstream\n{content}\nendstream"]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{body}\nendobj\n".encode("latin-1"))
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()

# ── meta tags ───────────────────────────────────────────────────────────────────────────────────────────────────────
@utility("gen.meta", "gen", "Мета-теги и Open Graph", "Meta tags and Open Graph",
         "Готовый блок <head>: title, description, Open Graph, Twitter Card, canonical, favicon — для сайта или лендинга.",
         "A ready <head> block: title, description, Open Graph, Twitter Card, canonical, favicon — for a site or landing page.",
         input="none", opts=[O("title", "text", "Заголовок", "Title", ""), O("desc", "textarea", "Описание", "Description", ""),
                             O("url", "text", "Адрес страницы", "Page URL", "https://example.com/"), O("image", "text", "Картинка для превью (URL)", "Preview image URL", ""),
                             O("site", "text", "Название сайта", "Site name", ""), O("lang", "text", "Язык (ru_RU, en_US)", "Locale (ru_RU, en_US)", "ru_RU")],
         live=True, keys="мета теги open graph og seo head twitter card")
def gen_meta(ctx):
    from html import escape
    o = ctx.o
    if not o["title"]:
        raise UtilError("Укажите заголовок", "Enter a title")
    t, d = escape(o["title"]), escape(o["desc"])
    rows = ['<meta charset="utf-8">', '<meta name="viewport" content="width=device-width, initial-scale=1">', f"<title>{t}</title>"]
    if d:
        rows.append(f'<meta name="description" content="{d}">')
    if o["url"]:
        rows.append(f'<link rel="canonical" href="{escape(o["url"])}">')
    rows += [f'<meta property="og:type" content="website">', f'<meta property="og:title" content="{t}">']
    if d:
        rows.append(f'<meta property="og:description" content="{d}">')
    if o["url"]:
        rows.append(f'<meta property="og:url" content="{escape(o["url"])}">')
    if o["image"]:
        rows += [f'<meta property="og:image" content="{escape(o["image"])}">', '<meta property="og:image:width" content="1200">', '<meta property="og:image:height" content="630">']
    if o["site"]:
        rows.append(f'<meta property="og:site_name" content="{escape(o["site"])}">')
    rows.append(f'<meta property="og:locale" content="{escape(o["lang"])}">')
    rows += [f'<meta name="twitter:card" content="{"summary_large_image" if o["image"] else "summary"}">', f'<meta name="twitter:title" content="{t}">']
    if d:
        rows.append(f'<meta name="twitter:description" content="{d}">')
    if o["image"]:
        rows.append(f'<meta name="twitter:image" content="{escape(o["image"])}">')
    rows += ['<link rel="icon" href="/favicon.ico" sizes="any">', '<link rel="apple-touch-icon" href="/apple-touch-icon.png">']
    note = ""
    if len(o["title"]) > 60:
        note = ctx.tr("Заголовок длиннее 60 символов — поисковики его обрежут", "The title is over 60 characters — search engines will cut it")
    elif len(o["desc"]) > 160:
        note = ctx.tr("Описание длиннее 160 символов — поисковики его обрежут", "The description is over 160 characters — search engines will cut it")
    return {"text": "\n".join(rows), "lang": "html", "note": note}
