# -*- coding: utf-8 -*-
"""Validators (JSON, email, cards, IBAN, Russian IDs, ISBN, passwords, phones…) and converters (units, bases, time, colors…)."""
from __future__ import annotations

import io
import json
import math
import random
import re
import uuid as _uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from .core import O, UtilError, as_float, lines_of, table, utility

# ── JSON ────────────────────────────────────────────────────────────────────────────────────────────────────────────
@utility("val.json", "check", "Проверка JSON", "JSON validator",
         "Проверяет JSON и точно показывает, где ошибка: строка, столбец и фрагмент с указателем.",
         "Validates JSON and pinpoints the error: line, column and the snippet with a caret.",
         live=True, ai_default=True, keys="json валидатор проверить ошибка validate")
def val_json(ctx):
    text = ctx.need_text()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        line = text.split("\n")[exc.lineno - 1] if exc.lineno - 1 < len(text.split("\n")) else ""
        caret = " " * max(0, exc.colno - 1) + "^"
        return {"text": f"{line}\n{caret}", "note": ctx.tr(f"✗ Ошибка: {exc.msg} — строка {exc.lineno}, столбец {exc.colno}",
                                                          f"✗ Error: {exc.msg} — line {exc.lineno}, column {exc.colno}")}
    def depth(v, d=0):
        if isinstance(v, dict):
            return max([depth(x, d + 1) for x in v.values()] or [d + 1])
        if isinstance(v, list):
            return max([depth(x, d + 1) for x in v] or [d + 1])
        return d
    kind = {dict: ctx.tr("объект", "object"), list: ctx.tr("массив", "array")}.get(type(value), type(value).__name__)
    size = len(value) if isinstance(value, (dict, list)) else 1
    return {"note": ctx.tr("✓ Корректный JSON", "✓ Valid JSON"),
            "info": [[ctx.tr("Тип", "Type"), kind], [ctx.tr("Элементов верхнего уровня", "Top-level items"), size], [ctx.tr("Глубина", "Depth"), depth(value)]]}

# ── email ───────────────────────────────────────────────────────────────────────────────────────────────────────────
_EMAIL = re.compile(r"^(?=.{3,254}$)[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*@(?:[A-Za-z0-9\u0400-\u04ff](?:[A-Za-z0-9\u0400-\u04ff-]{0,61}[A-Za-z0-9\u0400-\u04ff])?\.)+[A-Za-z\u0400-\u04ff]{2,63}$")
_TYPOS = {"gmial.com": "gmail.com", "gmal.com": "gmail.com", "gmail.ru": "gmail.com", "gamil.com": "gmail.com", "gnail.com": "gmail.com",
          "yandex.ry": "yandex.ru", "yandx.ru": "yandex.ru", "yanex.ru": "yandex.ru", "mail.ry": "mail.ru", "mial.ru": "mail.ru",
          "hotmial.com": "hotmail.com", "outlok.com": "outlook.com", "yahooo.com": "yahoo.com", "rambler.ry": "rambler.ru", "icloud.ru": "icloud.com"}

@utility("val.email", "check", "Проверка email-адресов", "Email address checker",
         "Проверяет адреса построчно: синтаксис, опечатки в домене (gmial.com), дубли; по желанию — есть ли у домена почтовый сервер.",
         "Checks addresses line by line: syntax, domain typos (gmial.com), duplicates; optionally whether the domain has a mail server.",
         opts=[O("mx", "bool", "Проверить MX-запись домена (нужен интернет)", "Check the domain MX record (needs internet)", False)],
         live=True, ai_default=True, keys="email почта проверить адрес валидация validate")
def val_email(ctx):
    rows, seen = [], set()
    mx_cache: Dict[str, str] = {}
    for line in lines_of(ctx.need_text("Введите адреса, по одному в строке", "Enter addresses, one per line")):
        addr = line.strip().strip(",;<>")
        if not addr:
            continue
        status = "✓" if _EMAIL.match(addr) else "✗ " + ctx.tr("неверный формат", "bad format")
        domain = addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""
        hint = ""
        if domain in _TYPOS:
            hint = ctx.tr(f"возможно, {_TYPOS[domain]}?", f"did you mean {_TYPOS[domain]}?")
        if addr.lower() in seen:
            hint = (hint + "; " if hint else "") + ctx.tr("повтор", "duplicate")
        seen.add(addr.lower())
        if ctx.o["mx"] and status == "✓" and domain:
            if domain not in mx_cache:
                from .t_net import doh_query
                try:
                    answers = doh_query(domain, "MX")
                    mx_cache[domain] = ", ".join(sorted(a.split()[-1].rstrip(".") for a in answers)[:2]) or ctx.tr("нет MX", "no MX")
                except Exception:  # noqa: BLE001
                    mx_cache[domain] = ctx.tr("не удалось проверить", "lookup failed")
            hint = (hint + "; " if hint else "") + "MX: " + mx_cache[domain]
        rows.append([addr, status, hint])
    ok = sum(1 for r in rows if r[1] == "✓")
    return {"table": table([ctx.tr("Адрес", "Address"), ctx.tr("Статус", "Status"), ctx.tr("Замечание", "Note")], rows),
            "note": ctx.tr(f"Корректных: {ok} из {len(rows)}", f"Valid: {ok} of {len(rows)}")}

# ── cards / IBAN ────────────────────────────────────────────────────────────────────────────────────────────────────
def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0

def card_brand(digits: str) -> str:
    for brand, rx in (("Мир / Mir", r"220[0-4]"), ("Visa", r"4"), ("Mastercard", r"5[1-5]|2(?:2[2-9]|[3-6]\d|7[01]|720)"),
                      ("American Express", r"3[47]"), ("UnionPay", r"62"), ("JCB", r"35(?:2[89]|[3-8])"), ("Maestro", r"5[06-9]|6[37]"),
                      ("Discover", r"6(?:011|5)"), ("Diners Club", r"3(?:0[0-5]|[68])")):
        if re.match(rx, digits):
            return brand
    return "—"

@utility("val.card", "check", "Проверка номера карты", "Card number checker",
         "Алгоритм Луна и платёжная система (Мир, Visa, Mastercard…). Данные никуда не отправляются.",
         "Luhn check and the payment network (Mir, Visa, Mastercard…). Nothing is sent anywhere.",
         live=True, keys="карта номер карты луна luhn visa mastercard мир card")
def val_card(ctx):
    rows = []
    for line in lines_of(ctx.need_text("Введите номер карты", "Enter a card number")):
        digits = re.sub(r"\D", "", line)
        if not digits:
            continue
        ok = 12 <= len(digits) <= 19 and luhn_ok(digits)
        masked = digits[:6] + "•" * (len(digits) - 10) + digits[-4:] if len(digits) > 10 else digits
        rows.append([masked, card_brand(digits), len(digits), ctx.tr("✓ корректный", "✓ valid") if ok else ctx.tr("✗ ошибка в номере", "✗ invalid")])
    return {"table": table([ctx.tr("Номер", "Number"), ctx.tr("Система", "Network"), ctx.tr("Цифр", "Digits"), ctx.tr("Луна", "Luhn")], rows)}

_IBAN_LEN = {"AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16, "BG": 22, "BH": 22, "BY": 28, "CH": 21, "CY": 28, "CZ": 24,
             "DE": 22, "DK": 18, "EE": 20, "ES": 24, "FI": 18, "FR": 27, "GB": 22, "GE": 22, "GR": 27, "HR": 21, "HU": 28, "IE": 22, "IL": 23,
             "IS": 26, "IT": 27, "KZ": 20, "LT": 20, "LU": 20, "LV": 21, "MC": 27, "MD": 24, "ME": 22, "MT": 31, "NL": 18, "NO": 15, "PL": 28,
             "PT": 25, "RO": 24, "RS": 22, "RU": 33, "SA": 24, "SE": 24, "SI": 19, "SK": 24, "TR": 26, "UA": 29}

@utility("val.iban", "check", "Проверка IBAN", "IBAN checker",
         "Контрольная сумма mod-97, длина для страны и форматирование группами по 4.",
         "mod-97 checksum, per-country length and grouping by 4.",
         live=True, keys="iban банк счёт bank account")
def val_iban(ctx):
    rows = []
    for line in lines_of(ctx.need_text("Введите IBAN", "Enter an IBAN")):
        iban = re.sub(r"[\s-]", "", line).upper()
        if not iban:
            continue
        country = iban[:2]
        ok_len = _IBAN_LEN.get(country) == len(iban) if country in _IBAN_LEN else 15 <= len(iban) <= 34
        moved = iban[4:] + iban[:4]
        numeric = "".join(str(int(ch, 36)) for ch in moved if ch.isalnum())
        ok = bool(re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", iban)) and ok_len and int(numeric) % 97 == 1
        rows.append([" ".join(iban[i:i + 4] for i in range(0, len(iban), 4)), country, ctx.tr("✓ корректный", "✓ valid") if ok else ctx.tr("✗ ошибка", "✗ invalid")])
    return {"table": table(["IBAN", ctx.tr("Страна", "Country"), ctx.tr("Проверка", "Check")], rows)}

# ── Russian identifiers ─────────────────────────────────────────────────────────────────────────────────────────────
def _inn_digit(digits: str, weights) -> int:
    return sum(int(d) * w for d, w in zip(digits, weights)) % 11 % 10

def check_inn(s: str) -> bool:
    if len(s) == 10:
        return _inn_digit(s, (2, 4, 10, 3, 5, 9, 4, 6, 8)) == int(s[9])
    if len(s) == 12:
        return (_inn_digit(s, (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)) == int(s[10]) and
                _inn_digit(s, (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)) == int(s[11]))
    return False

def snils_check(s: str) -> int:
    total = sum(int(d) * (9 - i) for i, d in enumerate(s[:9]))
    return 0 if total % 101 in (100,) else total % 101 if total % 101 < 100 else 0

def check_snils(s: str) -> bool:
    if len(s) != 11:
        return False
    if int(s[:9]) <= 1001998:
        return True
    return snils_check(s) == int(s[9:])

def check_ogrn(s: str) -> bool:
    if len(s) == 13:
        return int(s[:12]) % 11 % 10 == int(s[12])
    if len(s) == 15:
        return int(s[:14]) % 13 % 10 == int(s[14])
    return False

def gen_inn(kind: int = 10) -> str:
    rnd = random.SystemRandom()
    region = f"{rnd.randint(1, 99):02d}"
    if kind == 10:
        base = region + "".join(str(rnd.randint(0, 9)) for _ in range(7))
        return base + str(_inn_digit(base, (2, 4, 10, 3, 5, 9, 4, 6, 8)))
    base = region + "".join(str(rnd.randint(0, 9)) for _ in range(8))
    base += str(_inn_digit(base, (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)))
    return base + str(_inn_digit(base, (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)))

def gen_snils() -> str:
    rnd = random.SystemRandom()
    while True:
        base = "".join(str(rnd.randint(0, 9)) for _ in range(9))
        if int(base) > 1001998 and not re.search(r"(\d)\1\1", base):
            break
    s = base + f"{snils_check(base + '00'):02d}"
    return f"{s[:3]}-{s[3:6]}-{s[6:9]} {s[9:]}"

def gen_ogrn(kind: int = 13) -> str:
    rnd = random.SystemRandom()
    if kind == 13:
        base = str(rnd.choice((1, 5))) + f"{rnd.randint(2, 24):02d}" + f"{rnd.randint(1, 99):02d}" + "".join(str(rnd.randint(0, 9)) for _ in range(7))
        return base + str(int(base) % 11 % 10)
    base = "3" + f"{rnd.randint(4, 24):02d}" + f"{rnd.randint(1, 99):02d}" + "".join(str(rnd.randint(0, 9)) for _ in range(9))
    return base + str(int(base) % 13 % 10)

@utility("val.ru_ids", "check", "ИНН, СНИЛС, ОГРН, КПП, БИК: проверка и генерация", "Russian IDs: INN, SNILS, OGRN — check & generate",
         "Проверяет контрольные числа ИНН (10/12), СНИЛС, ОГРН/ОГРНИП, формат КПП и БИК; генерирует тестовые номера.",
         "Validates Russian INN (10/12), SNILS, OGRN/OGRNIP check digits, KPP and BIK formats; generates test numbers.",
         opts=[O("mode", "choice", "Действие", "Action", "check", choices=[("check", "Проверить", "Check"), ("generate", "Сгенерировать тестовые", "Generate test numbers")]),
               O("kind", "choice", "Что генерировать", "Generate", "inn10", show="mode=generate",
                 choices=[("inn10", "ИНН организации (10)", "Company INN (10)"), ("inn12", "ИНН физлица (12)", "Personal INN (12)"), ("snils", "СНИЛС", "SNILS"),
                          ("ogrn", "ОГРН", "OGRN"), ("ogrnip", "ОГРНИП", "OGRNIP")]),
               O("count", "int", "Сколько", "How many", 5, min=1, max=500, show="mode=generate")],
         live=True, ai_default=True, keys="инн снилс огрн кпп бик проверка реквизиты inn snils")
def val_ru_ids(ctx):
    if ctx.o["mode"] == "generate":
        n, kind = int(ctx.o["count"]), ctx.o["kind"]
        make = {"inn10": lambda: gen_inn(10), "inn12": lambda: gen_inn(12), "snils": gen_snils, "ogrn": lambda: gen_ogrn(13), "ogrnip": lambda: gen_ogrn(15)}[kind]
        return {"text": "\n".join(make() for _ in range(n)), "note": ctx.tr("Номера синтетические — только для тестов", "Synthetic numbers — for testing only")}
    rows = []
    for line in lines_of(ctx.need_text("Введите номера, по одному в строке", "Enter numbers, one per line")):
        raw = line.strip()
        digits = re.sub(r"\D", "", raw)
        if not digits:
            continue
        kinds = []
        if len(digits) in (10, 12):
            kinds.append((ctx.tr("ИНН", "INN"), check_inn(digits)))
        if len(digits) == 11:
            kinds.append((ctx.tr("СНИЛС", "SNILS"), check_snils(digits)))
        if len(digits) == 13:
            kinds.append((ctx.tr("ОГРН", "OGRN"), check_ogrn(digits)))
        if len(digits) == 15:
            kinds.append((ctx.tr("ОГРНИП", "OGRNIP"), check_ogrn(digits)))
        if len(digits) == 9:
            if digits.startswith("04"):
                kinds.append((ctx.tr("БИК (формат)", "BIK (format)"), True))
            kinds.append((ctx.tr("КПП (формат)", "KPP (format)"), bool(re.fullmatch(r"\d{4}[\dA-Z]{2}\d{3}", raw.replace(" ", "")))))
        if not kinds:
            kinds.append((ctx.tr("неизвестно", "unknown"), False))
        for name, ok in kinds:
            rows.append([raw, name, ctx.tr("✓ контрольное число верно", "✓ check digit OK") if ok else ctx.tr("✗ ошибка", "✗ invalid")])
    return {"table": table([ctx.tr("Номер", "Number"), ctx.tr("Тип", "Type"), ctx.tr("Проверка", "Check")], rows)}

# ── ISBN / EAN ──────────────────────────────────────────────────────────────────────────────────────────────────────
def ean_check_digit(body: str) -> int:
    total = sum(int(d) * (1 if i % 2 else 3) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10

@utility("val.isbn", "check", "ISBN, EAN, UPC: проверка и пересчёт", "ISBN, EAN, UPC: check and convert",
         "Проверяет контрольную цифру ISBN-10/13, EAN-8/13 и UPC-A, переводит ISBN-10 ↔ ISBN-13.",
         "Validates ISBN-10/13, EAN-8/13 and UPC-A check digits and converts ISBN-10 ↔ ISBN-13.",
         live=True, keys="isbn ean upc штрихкод barcode книга")
def val_isbn(ctx):
    rows = []
    for line in lines_of(ctx.need_text("Введите ISBN или штрихкод", "Enter an ISBN or barcode")):
        code = re.sub(r"[\s-]", "", line).upper()
        if not code:
            continue
        kind, ok, other = "—", False, ""
        if len(code) == 10 and re.fullmatch(r"\d{9}[\dX]", code):
            kind = "ISBN-10"
            ok = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(code)) % 11 == 0
            body = "978" + code[:9]
            other = "ISBN-13: " + body + str(ean_check_digit(body))
        elif re.fullmatch(r"\d{13}", code):
            kind = "ISBN-13" if code.startswith(("978", "979")) else "EAN-13"
            ok = ean_check_digit(code[:12]) == int(code[12])
            if code.startswith("978"):
                body = code[3:12]
                s = sum((10 - i) * int(c) for i, c in enumerate(body)) % 11
                check = (11 - s) % 11
                other = "ISBN-10: " + body + ("X" if check == 10 else str(check))
        elif re.fullmatch(r"\d{12}", code):
            kind, ok = "UPC-A", ean_check_digit(code[:11]) == int(code[11])
        elif re.fullmatch(r"\d{8}", code):
            kind, ok = "EAN-8", ean_check_digit(code[:7]) == int(code[7])
        rows.append([line.strip(), kind, ctx.tr("✓ верно", "✓ valid") if ok else ctx.tr("✗ ошибка", "✗ invalid"), other])
    return {"table": table([ctx.tr("Код", "Code"), ctx.tr("Тип", "Type"), ctx.tr("Проверка", "Check"), ctx.tr("Другой вид", "Other form")], rows)}

# ── password strength ───────────────────────────────────────────────────────────────────────────────────────────────
_COMMON = set("123456 password 12345678 qwerty 123456789 12345 1234 111111 1234567 dragon 123123 baseball abc123 football monkey letmein "
              "696969 shadow master 666666 qwertyuiop 123321 mustang 1234567890 michael 654321 superman 1qaz2wsx 7777777 121212 000000 "
              "qazwsx 123qwe killer trustno1 jordan jennifer zxcvbnm asdfgh hunter buster soccer harley batman andrew tigger sunshine "
              "iloveyou 2000 charlie robert thomas hockey ranger daniel starwars klaster 112233 george computer michelle jessica pepper "
              "1111 zxcvbn 555555 11111111 131313 freedom 777777 pass maggie 159753 aaaaaa ginger princess joshua cheese amanda summer "
              "love ashley nicole chelsea biteme matthew access yankees 987654321 dallas austin thunder taylor matrix admin пароль "
              "йцукен qwerty123 password1 1q2w3e4r 1q2w3e welcome login".split())

def password_report(pw: str, en: bool) -> Tuple[float, List[str], str]:
    pools = [(r"[a-z]", 26), (r"[A-Z]", 26), (r"\d", 10), (r"[а-яё]", 33), (r"[А-ЯЁ]", 33), (r"[^\w\s]|_", 33), (r"\s", 1)]
    pool = sum(size for rx, size in pools if re.search(rx, pw))
    bits = len(pw) * math.log2(pool) if pool else 0
    issues = []
    tr = (lambda ru, e: e if en else ru)
    if pw.lower() in _COMMON:
        bits = min(bits, 10)
        issues.append(tr("в списке самых частых паролей", "in the most-common passwords list"))
    if re.search(r"(.)\1{2,}", pw):
        bits -= 6
        issues.append(tr("повторяющиеся символы", "repeated characters"))
    if re.search(r"(?:0123|1234|2345|3456|4567|5678|6789|abcd|qwer|asdf|zxcv|йцук|фыва)", pw.lower()):
        bits -= 10
        issues.append(tr("последовательность на клавиатуре/цифрах", "keyboard or digit sequence"))
    if re.fullmatch(r"(19|20)\d{2}|\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}", pw):
        bits = min(bits, 20)
        issues.append(tr("похоже на дату", "looks like a date"))
    if len(pw) < 12:
        issues.append(tr("короче 12 символов", "shorter than 12 characters"))
    bits = max(0.0, bits)
    guesses = 2 ** bits / 2
    seconds = guesses / 1e10
    return bits, issues, _crack_time(seconds, en)

def _crack_time(seconds: float, en: bool) -> str:
    for size, ru, e in ((3.15e16, "млрд лет", "billion years"), (3.15e13, "млн лет", "million years"), (3.15e10, "тыс. лет", "thousand years"),
                        (3.15e7, "лет", "years"), (86400 * 30, "мес.", "months"), (86400, "дн.", "days"), (3600, "ч", "hours"), (60, "мин", "minutes"), (1, "с", "seconds")):
        if seconds >= size:
            value = seconds / size
            return f"~{value:,.0f} {e if en else ru}".replace(",", " ") if value < 1e6 else (f"> 1 000 000 {e if en else ru}")
    return "instantly" if en else "мгновенно"

@utility("val.password", "check", "Надёжность пароля", "Password strength",
         "Энтропия, время подбора на мощной видеокарте и конкретные слабые места. Пароль не покидает компьютер.",
         "Entropy, time to crack on a strong GPU rig and concrete weaknesses. The password never leaves the computer.",
         live=True, keys="пароль надёжность сложность энтропия password strength")
def val_password(ctx):
    pw = ctx.text or ""
    if not pw:
        raise UtilError("Введите пароль", "Enter a password")
    bits, issues, crack = password_report(pw, ctx.en)
    level = (ctx.tr("очень слабый", "very weak") if bits < 28 else ctx.tr("слабый", "weak") if bits < 45 else
             ctx.tr("средний", "fair") if bits < 60 else ctx.tr("надёжный", "strong") if bits < 80 else ctx.tr("очень надёжный", "very strong"))
    return {"info": [[ctx.tr("Оценка", "Rating"), level], [ctx.tr("Энтропия", "Entropy"), f"{bits:.0f} bit"], [ctx.tr("Длина", "Length"), len(pw)],
                     [ctx.tr("Время подбора (10¹⁰ попыток/с)", "Time to crack (10¹⁰ guesses/s)"), crack],
                     [ctx.tr("Слабые места", "Weaknesses"), "; ".join(issues) or ctx.tr("не найдено", "none found")]]}

# ── phone ───────────────────────────────────────────────────────────────────────────────────────────────────────────
_CODES = {"7": "Россия / Казахстан", "375": "Беларусь", "380": "Украина", "374": "Армения", "994": "Азербайджан", "995": "Грузия",
          "996": "Кыргызстан", "992": "Таджикистан", "993": "Туркменистан", "998": "Узбекистан", "373": "Молдова", "371": "Латвия",
          "370": "Литва", "372": "Эстония", "1": "США / Канада", "44": "Великобритания", "49": "Германия", "33": "Франция",
          "39": "Италия", "34": "Испания", "90": "Турция", "86": "Китай", "91": "Индия", "81": "Япония", "82": "Корея",
          "971": "ОАЭ", "972": "Израиль", "48": "Польша", "420": "Чехия", "31": "Нидерланды", "41": "Швейцария", "66": "Таиланд",
          "84": "Вьетнам", "62": "Индонезия", "55": "Бразилия", "52": "Мексика", "61": "Австралия"}

@utility("val.phone", "check", "Нормализация телефонов", "Phone number normalizer",
         "Приводит номера к одному виду: +7 (912) 345-67-89, E.164 (+79123456789) или 8XXXXXXXXXX; определяет страну.",
         "Normalizes numbers: +7 (912) 345-67-89, E.164 (+79123456789) or national; detects the country.",
         opts=[O("format", "choice", "Формат", "Format", "pretty", choices=[("pretty", "+7 (912) 345-67-89", "+7 (912) 345-67-89"), ("e164", "E.164: +79123456789", "E.164: +79123456789"),
                                                                     ("ru8", "8 912 345-67-89", "8 912 345-67-89"), ("digits", "Только цифры", "Digits only")])],
         live=True, ai_default=True, keys="телефон номер нормализация phone e164 формат")
def val_phone(ctx):
    rows = []
    for line in lines_of(ctx.need_text("Введите номера, по одному в строке", "Enter numbers, one per line")):
        raw = line.strip()
        digits = re.sub(r"\D", "", raw)
        if not digits:
            continue
        if len(digits) == 11 and digits[0] == "8" and not raw.startswith("+"):
            digits = "7" + digits[1:]
        elif len(digits) == 10 and digits[0] == "9":
            digits = "7" + digits
        code = next((c for c in sorted(_CODES, key=len, reverse=True) if digits.startswith(c)), "")
        ok = 10 <= len(digits) <= 15
        fmt = ctx.o["format"]
        if fmt == "e164":
            out = "+" + digits
        elif fmt == "digits":
            out = digits
        elif code == "7" and len(digits) == 11:
            n = digits[1:]
            out = f"+7 ({n[:3]}) {n[3:6]}-{n[6:8]}-{n[8:]}" if fmt == "pretty" else f"8 {n[:3]} {n[3:6]}-{n[6:8]}-{n[8:]}"
        else:
            rest = digits[len(code):]
            out = f"+{code} " + " ".join(rest[i:i + 3] for i in range(0, len(rest), 3)) if code else "+" + digits
        rows.append([raw, out, _CODES.get(code, "—") if not ctx.en else code and f"+{code}", "✓" if ok else ctx.tr("✗ длина", "✗ length")])
    return {"table": table([ctx.tr("Было", "Input"), ctx.tr("Стало", "Normalized"), ctx.tr("Страна", "Country"), ""], rows),
            "text": "\n".join(r[1] for r in rows)}

# ── semver / uuid ───────────────────────────────────────────────────────────────────────────────────────────────────
_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$")

def _semver_key(v: str):
    m = _SEMVER.match(v.strip())
    if not m:
        raise UtilError(f"«{v}» — не semver (нужно 1.2.3)", f"“{v}” is not semver (use 1.2.3)")
    pre = m.group(4)
    pre_key = tuple((0, int(p)) if p.isdigit() else (1, p) for p in pre.split(".")) if pre else ((2, ""),)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), pre_key)

@utility("val.semver", "check", "Версии semver: сравнить, отсортировать, поднять", "Semver: compare, sort, bump",
         "Сортирует версии правильно (1.10 > 1.9), сравнивает пары и подсказывает следующую major/minor/patch.",
         "Sorts versions correctly (1.10 > 1.9), compares pairs and suggests the next major/minor/patch.",
         live=True, keys="semver версия сравнить version compare bump")
def val_semver(ctx):
    versions = [v.strip() for v in re.split(r"[\s,;]+", ctx.need_text("Введите версии", "Enter versions")) if v.strip()]
    ordered = sorted(versions, key=_semver_key)
    out: Dict[str, Any] = {"text": "\n".join(ordered)}
    if len(versions) == 2:
        a, b = (_semver_key(v) for v in versions)
        rel = "=" if a == b else "<" if a < b else ">"
        out["note"] = f"{versions[0]} {rel} {versions[1]}"
    latest = _SEMVER.match(ordered[-1].strip())
    ma, mi, pa = int(latest.group(1)), int(latest.group(2)), int(latest.group(3))
    out["info"] = [[ctx.tr("Самая новая", "Latest"), ordered[-1]], ["patch", f"{ma}.{mi}.{pa + 1}"], ["minor", f"{ma}.{mi + 1}.0"], ["major", f"{ma + 1}.0.0"]]
    return out

@utility("val.uuid", "check", "Разобрать UUID / ULID", "Inspect a UUID / ULID",
         "Версия, вариант и зашитое время создания (UUID v1/v6/v7, ULID); проверка формата.",
         "Version, variant and the embedded creation time (UUID v1/v6/v7, ULID); format check.",
         live=True, keys="uuid guid ulid версия время разобрать")
def val_uuid(ctx):
    rows = []
    for line in lines_of(ctx.need_text("Вставьте UUID", "Paste a UUID")):
        raw = line.strip().strip("{}")
        if not raw:
            continue
        if re.fullmatch(r"[0-9A-HJKMNP-TV-Za-hjkmnp-tv-z]{26}", raw) and not re.fullmatch(r"[0-9a-fA-F]{26}", raw):
            alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
            ms = 0
            for ch in raw[:10].upper():
                ms = ms * 32 + alphabet.index(ch)
            rows.append([raw, "ULID", "—", datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " UTC"])
            continue
        try:
            u = _uuid.UUID(raw)
        except ValueError:
            rows.append([raw, ctx.tr("✗ не UUID", "✗ not a UUID"), "", ""])
            continue
        when = ""
        if u.version == 1:
            when = datetime(1582, 10, 15, tzinfo=timezone.utc) + timedelta(microseconds=u.time // 10)
        elif u.version == 7:
            when = datetime.fromtimestamp(int(u.hex[:12], 16) / 1000, timezone.utc)
        elif u.version == 6:
            t = int(u.hex[:12] + u.hex[13:16], 16)
            when = datetime(1582, 10, 15, tzinfo=timezone.utc) + timedelta(microseconds=t // 10)
        variant = {"specified in RFC 4122": "RFC 4122/9562", "reserved for NCS compatibility": "NCS",
                   "reserved for Microsoft compatibility": "Microsoft", "reserved for future definition": "future"}.get(u.variant, u.variant)
        nil = " (nil)" if u.int == 0 else " (max)" if u.int == 2 ** 128 - 1 else ""
        rows.append([str(u) + nil, f"v{u.version}" if u.version else "—", variant, when.strftime("%Y-%m-%d %H:%M:%S UTC") if when else "—"])
    return {"table": table(["UUID", ctx.tr("Версия", "Version"), ctx.tr("Вариант", "Variant"), ctx.tr("Время создания", "Created")], rows)}

# ── units ───────────────────────────────────────────────────────────────────────────────────────────────────────────
UNITS: Dict[str, Dict[str, Tuple[float, str, str]]] = {
    "length": {"mm": (0.001, "мм", "mm"), "cm": (0.01, "см", "cm"), "m": (1, "м", "m"), "km": (1000, "км", "km"), "in": (0.0254, "дюйм", "in"),
               "ft": (0.3048, "фут", "ft"), "yd": (0.9144, "ярд", "yd"), "mi": (1609.344, "миля", "mile"), "nmi": (1852, "морская миля", "nautical mile"),
               "um": (1e-6, "мкм", "µm"), "ly": (9.4607e15, "световой год", "light year")},
    "mass": {"mg": (1e-6, "мг", "mg"), "g": (0.001, "г", "g"), "kg": (1, "кг", "kg"), "t": (1000, "т", "t"), "oz": (0.028349523125, "унция", "oz"),
             "lb": (0.45359237, "фунт", "lb"), "st": (6.35029318, "стоун", "stone"), "ct": (0.0002, "карат", "carat")},
    "area": {"mm2": (1e-6, "мм²", "mm²"), "cm2": (1e-4, "см²", "cm²"), "m2": (1, "м²", "m²"), "ha": (1e4, "га", "ha"), "sotka": (100, "сотка", "are (100 m²)"),
             "km2": (1e6, "км²", "km²"), "in2": (0.00064516, "дюйм²", "in²"), "ft2": (0.09290304, "фут²", "ft²"), "acre": (4046.8564224, "акр", "acre")},
    "volume": {"ml": (1e-6, "мл", "ml"), "l": (0.001, "л", "l"), "m3": (1, "м³", "m³"), "tsp": (4.92892e-6, "ч. ложка", "tsp"), "tbsp": (1.478676e-5, "ст. ложка", "tbsp"),
               "cup": (2.365882e-4, "чашка (US)", "cup (US)"), "floz": (2.957353e-5, "жидк. унция", "fl oz"), "gal": (0.003785411784, "галлон (US)", "gallon (US)"),
               "pt": (4.731765e-4, "пинта (US)", "pint (US)"), "bbl": (0.158987294928, "баррель", "barrel")},
    "speed": {"ms": (1, "м/с", "m/s"), "kmh": (1 / 3.6, "км/ч", "km/h"), "mph": (0.44704, "миль/ч", "mph"), "kn": (0.514444, "узел", "knot"), "mach": (340.29, "мах", "Mach")},
    "time": {"ms": (0.001, "мс", "ms"), "s": (1, "с", "s"), "min": (60, "мин", "min"), "h": (3600, "ч", "h"), "d": (86400, "сут", "day"), "wk": (604800, "нед", "week"),
             "mo": (2629746, "мес (ср.)", "month (avg)"), "yr": (31556952, "год", "year")},
    "data": {"bit": (0.125, "бит", "bit"), "B": (1, "байт", "byte"), "KB": (1e3, "КБ (10³)", "kB"), "MB": (1e6, "МБ (10⁶)", "MB"), "GB": (1e9, "ГБ (10⁹)", "GB"),
             "TB": (1e12, "ТБ (10¹²)", "TB"), "KiB": (1024, "КиБ (2¹⁰)", "KiB"), "MiB": (1024 ** 2, "МиБ (2²⁰)", "MiB"), "GiB": (1024 ** 3, "ГиБ (2³⁰)", "GiB"),
             "TiB": (1024 ** 4, "ТиБ (2⁴⁰)", "TiB"), "Mbit": (125000, "Мбит", "Mbit")},
    "pressure": {"pa": (1, "Па", "Pa"), "kpa": (1000, "кПа", "kPa"), "mpa": (1e6, "МПа", "MPa"), "bar": (1e5, "бар", "bar"), "atm": (101325, "атм", "atm"),
                 "mmhg": (133.322, "мм рт. ст.", "mmHg"), "psi": (6894.757, "psi", "psi"), "kgfcm2": (98066.5, "кгс/см²", "kgf/cm²")},
    "energy": {"j": (1, "Дж", "J"), "kj": (1000, "кДж", "kJ"), "cal": (4.184, "кал", "cal"), "kcal": (4184, "ккал", "kcal"), "wh": (3600, "Вт·ч", "Wh"),
               "kwh": (3.6e6, "кВт·ч", "kWh"), "ev": (1.602176634e-19, "эВ", "eV"), "btu": (1055.06, "BTU", "BTU")},
    "power": {"w": (1, "Вт", "W"), "kw": (1000, "кВт", "kW"), "mw": (1e6, "МВт", "MW"), "hp": (735.49875, "л.с.", "hp (metric)"), "hpi": (745.699872, "hp (имп.)", "hp (imperial)")},
    "angle": {"deg": (1, "градус", "degree"), "rad": (180 / math.pi, "радиан", "radian"), "grad": (0.9, "град", "gradian"), "arcmin": (1 / 60, "угл. минута", "arcminute"),
              "arcsec": (1 / 3600, "угл. секунда", "arcsecond"), "turn": (360, "оборот", "turn")},
    "fuel": {"l100": (1, "л/100 км", "l/100 km"), "kml": (-1, "км/л", "km/l"), "mpg": (-2, "миль/галлон (US)", "mpg (US)")},
    "temperature": {"c": (0, "°C", "°C"), "f": (0, "°F", "°F"), "k": (0, "K", "K"), "r": (0, "°R", "°R")},
}
_GROUP_NAMES = {"length": ("Длина", "Length"), "mass": ("Масса", "Mass"), "area": ("Площадь", "Area"), "volume": ("Объём", "Volume"),
                "speed": ("Скорость", "Speed"), "time": ("Время", "Time"), "data": ("Данные", "Data"), "pressure": ("Давление", "Pressure"),
                "energy": ("Энергия", "Energy"), "power": ("Мощность", "Power"), "angle": ("Угол", "Angle"), "fuel": ("Расход топлива", "Fuel economy"),
                "temperature": ("Температура", "Temperature")}

def _to_celsius(v: float, unit: str) -> float:
    return {"c": v, "f": (v - 32) * 5 / 9, "k": v - 273.15, "r": (v - 491.67) * 5 / 9}[unit]

def _from_celsius(c: float, unit: str) -> float:
    return {"c": c, "f": c * 9 / 5 + 32, "k": c + 273.15, "r": (c + 273.15) * 9 / 5}[unit]

def _fuel_to_l100(v: float, unit: str) -> float:
    if unit == "l100":
        return v
    if unit == "kml":
        return 100 / v
    return 235.214583 / v

def _find_unit(token: str) -> Optional[Tuple[str, str]]:
    t = token.strip().lower().replace("²", "2").replace("³", "3").replace("°", "").replace(" ", "")
    aliases = {"км/ч": "kmh", "kmh": "kmh", "km/h": "kmh", "м/с": "ms", "m/s": "ms", "мм": "mm", "см": "cm", "м": "m", "км": "km", "дюйм": "in",
               "дюймы": "in", "\"": "in", "фут": "ft", "ft": "ft", "миля": "mi", "мили": "mi", "кг": "kg", "г": "g", "гр": "g", "мг": "mg", "т": "t",
               "фунт": "lb", "lbs": "lb", "унция": "oz", "л": "l", "мл": "ml", "м3": "m3", "м2": "m2", "га": "ha", "сотка": "sotka", "сотки": "sotka",
               "соток": "sotka", "км2": "km2", "с": "s", "сек": "s", "мин": "min", "ч": "h", "час": "h", "сут": "d", "день": "d", "дней": "d",
               "нед": "wk", "год": "yr", "лет": "yr", "c": "c", "цельсий": "c", "f": "f", "фаренгейт": "f", "k": "k", "кельвин": "k",
               "кб": "KB", "мб": "MB", "гб": "GB", "тб": "TB", "kb": "KB", "mb": "MB", "gb": "GB", "tb": "TB", "kib": "KiB", "mib": "MiB", "gib": "GiB",
               "tib": "TiB", "байт": "B", "b": "B", "бит": "bit", "мбит": "Mbit", "mbit": "Mbit", "вт": "w", "квт": "kw", "лс": "hp", "л.с.": "hp",
               "атм": "atm", "бар": "bar", "мм.рт.ст": "mmhg", "ммрт.ст.": "mmhg", "ккал": "kcal", "кал": "cal", "квтч": "kwh", "квт·ч": "kwh",
               "дж": "j", "кдж": "kj", "градус": "deg", "град": "deg", "рад": "rad", "узел": "kn", "узлов": "kn", "мах": "mach"}
    key = aliases.get(t, _UNIT_WORDS.get(t, t))
    for group, units in UNITS.items():
        for name in units:
            if name.lower() == key.lower():
                return group, name
    # the names the result prints («миля», «mile», «КБ (10³)») and plain plurals («miles», «feet») work too
    tries = [key.lower()]
    if tries[0].endswith("es") and len(tries[0]) > 4:
        tries.append(tries[0][:-2])
    if tries[0].endswith("s") and len(tries[0]) > 3:
        tries.append(tries[0][:-1])
    for group, units in UNITS.items():
        for name, row in units.items():
            shown = {re.sub(r"\s*\(.*?\)", "", str(label)).lower().replace(" ", "").replace("°", "") for label in row[1:3]}
            if any(item in shown for item in tries):
                return group, name
    return None

# whole words people type («5 километров в мили», «3 feet to meters»)
_UNIT_WORDS: Dict[str, str] = {
    **{w: "km" for w in ("километр", "километры", "километров", "километрах", "kilometer", "kilometers", "kilometre", "kilometres")},
    **{w: "m" for w in ("метр", "метры", "метров", "метрах", "meter", "meters", "metre", "metres")},
    **{w: "cm" for w in ("сантиметр", "сантиметры", "сантиметров", "centimeter", "centimeters", "centimetre", "centimetres")},
    **{w: "mm" for w in ("миллиметр", "миллиметры", "миллиметров", "millimeter", "millimeters", "millimetre", "millimetres")},
    **{w: "mi" for w in ("миль", "милях", "милю", "miles")},
    **{w: "ft" for w in ("футы", "футов", "футах", "foot", "feet")},
    **{w: "in" for w in ("дюймов", "дюймах", "inch", "inches")},
    **{w: "yd" for w in ("ярды", "ярдов", "yard", "yards")},
    **{w: "kg" for w in ("килограмм", "килограммы", "килограммов", "кило", "kilogram", "kilograms", "kilo", "kilos")},
    **{w: "g" for w in ("грамм", "граммы", "граммов", "gram", "grams")},
    **{w: "t" for w in ("тонна", "тонны", "тонн", "ton", "tons", "tonne", "tonnes")},
    **{w: "lb" for w in ("фунты", "фунтов", "pound", "pounds")},
    **{w: "oz" for w in ("унции", "унций", "ounce", "ounces")},
    **{w: "l" for w in ("литр", "литры", "литров", "liter", "liters", "litre", "litres")},
    **{w: "ml" for w in ("миллилитр", "миллилитры", "миллилитров", "milliliter", "milliliters", "millilitre", "millilitres")},
    **{w: "gal" for w in ("галлон", "галлоны", "галлонов", "gallons")},
    **{w: "s" for w in ("секунда", "секунды", "секунд", "sec", "second", "seconds")},
    **{w: "min" for w in ("минута", "минуты", "минут", "minute", "minutes")},
    **{w: "h" for w in ("часы", "часа", "часов", "hour", "hours", "hr")},
    **{w: "d" for w in ("сутки", "дни", "дня", "days")},
    **{w: "wk" for w in ("неделя", "недели", "недель", "weeks")},
    **{w: "yr" for w in ("года", "годы", "years")},
    **{w: "B" for w in ("байты", "байтов", "байта", "bytes")},
    **{w: "bit" for w in ("биты", "битов", "bits")},
    "килобайт": "KB", "килобайты": "KB", "мегабайт": "MB", "мегабайты": "MB", "мегабайтов": "MB",
    "гигабайт": "GB", "гигабайты": "GB", "гигабайтов": "GB", "терабайт": "TB", "терабайты": "TB",
    "миль/ч": "mph", "мильвчас": "mph", "кмвчас": "kmh", "kph": "kmh", "узлы": "kn", "knots": "kn",
    **{w: "deg" for w in ("градусы", "градусов", "degrees")},
    **{w: "c" for w in ("цельсия", "цельсию", "celsius")},
    **{w: "f" for w in ("фаренгейта", "фаренгейту", "fahrenheit")},
    **{w: "k" for w in ("кельвина", "кельвины", "кельвинов", "kelvin")},
}

def convert_units(value: float, group: str, src: str, dst: str) -> float:
    if group == "temperature":
        return _from_celsius(_to_celsius(value, src), dst)
    if group == "fuel":
        l100 = _fuel_to_l100(value, src)
        return l100 if dst == "l100" else (100 / l100 if dst == "kml" else 235.214583 / l100)
    return value * UNITS[group][src][0] / UNITS[group][dst][0]

def _fmt_num(v: float, ctx=None) -> str:
    """A readable number: groups of thousands, up to 6 decimals; a decimal comma for Russian."""
    if v == 0:
        return "0"
    if abs(v) >= 1e15 or abs(v) < 1e-6:
        s = f"{v:.6g}"
    else:
        s = f"{v:,.6f}".rstrip("0").rstrip(".").replace(",", " ")
    return s.replace(".", ",") if ctx is not None and not ctx.en else s

@utility("conv.units", "convert", "Конвертер единиц", "Unit converter",
         "Длина, масса, площадь, объём, скорость, время, данные, давление, энергия, мощность, угол, топливо, температура. Можно писать «5 км в мили».",
         "Length, mass, area, volume, speed, time, data, pressure, energy, power, angle, fuel, temperature. Type “5 km to mi” too.",
         opts=[O("group", "choice", "Величина", "Quantity", "length", choices=[(k, v[0], v[1]) for k, v in _GROUP_NAMES.items()])],
         live=True, ai_default=True, placeholder="5 км в мили", placeholder_en="5 km to mi",
         keys="конвертер единиц перевести км мили кг фунты градусы unit converter")
def conv_units(ctx):
    text = ctx.need_text("Введите значение, например «5 км в мили» или просто 5", "Enter a value, e.g. “5 km to mi” or just 5").strip()
    m = re.match(r"^\s*([-+]?\d[\d\s]*(?:[.,]\d+)?(?:e[-+]?\d+)?)\s*([^\s\d].*?)?(?:\s+(?:в|to|in|->|→|=)\s+(.+))?\s*$", text, re.I)
    if not m:
        raise UtilError("Начните с числа: «5 км в мили»", "Start with a number: “5 km to mi”")
    value = as_float(m.group(1).replace(" ", ""))
    group, src = ctx.o["group"], None
    if m.group(2):
        found = _find_unit(m.group(2))
        if found:
            group, src = found
    units = UNITS[group]
    src = src or next(iter(units))
    if m.group(3):
        found = _find_unit(m.group(3))
        if not found or found[0] != group:
            raise UtilError(f"Не знаю единицу «{m.group(3)}» для этой величины", f"Unknown unit “{m.group(3)}” for this quantity")
        result = convert_units(value, group, src, found[1])
        return {"text": f"{_fmt_num(value, ctx)} {units[src][2 if ctx.en else 1]} = {_fmt_num(result, ctx)} {units[found[1]][2 if ctx.en else 1]}"}
    rows = [[units[u][2 if ctx.en else 1], _fmt_num(convert_units(value, group, src, u), ctx)] for u in units if u != src]
    return {"note": f"{_fmt_num(value, ctx)} {units[src][2 if ctx.en else 1]} =", "table": table([ctx.tr("Единица", "Unit"), ctx.tr("Значение", "Value")], rows)}

@utility("conv.base", "convert", "Системы счисления", "Number bases",
         "Число в двоичной, восьмеричной, десятичной, шестнадцатеричной и любой системе 2–36; понимает 0x, 0b, 0o.",
         "A number in binary, octal, decimal, hex and any base 2–36; understands 0x, 0b, 0o.",
         opts=[O("from", "int", "Исходная система (0 — определить по префиксу)", "From base (0 = by prefix)", 0, min=0, max=36),
               O("to", "int", "Своя система для вывода", "Extra target base", 36, min=2, max=36)],
         live=True, ai_default=True, placeholder="0xFF", keys="системы счисления двоичная шестнадцатеричная binary hex octal base")
def conv_base(ctx):
    rows = []
    for line in lines_of(ctx.need_text("Введите число", "Enter a number")):
        raw = line.strip().replace("_", "").replace(" ", "")
        if not raw:
            continue
        try:
            n = int(raw, int(ctx.o["from"])) if int(ctx.o["from"]) else int(raw, 0) if not re.fullmatch(r"0\d+", raw) else int(raw, 10)
        except ValueError:
            raise UtilError(f"«{raw}» не число в этой системе", f"“{raw}” is not a number in this base")
        def to_base(x: int, b: int) -> str:
            digits = "0123456789abcdefghijklmnopqrstuvwxyz"
            if x == 0:
                return "0"
            neg, x, out = x < 0, abs(x), ""
            while x:
                x, r = divmod(x, b)
                out = digits[r] + out
            return ("-" if neg else "") + out
        b = int(ctx.o["to"])
        rows += [["DEC", str(n)], ["HEX", hex(n)], ["OCT", oct(n)], ["BIN", bin(n)], [f"BASE{b}", to_base(n, b)]]
        if 0 <= n < 0x110000 and n >= 32:
            rows.append([ctx.tr("Символ", "Character"), chr(n)])
    return {"info": rows}

# ── time ────────────────────────────────────────────────────────────────────────────────────────────────────────────
def get_zone(name: str):
    name = (name or "").strip()
    if not name or name.lower() in ("local", "локальное", "мое", "моё"):
        return datetime.now().astimezone().tzinfo
    if name.upper() in ("UTC", "GMT", "Z"):
        return timezone.utc
    m = re.fullmatch(r"(?:UTC|GMT)?\s*([+-])(\d{1,2})(?::?(\d{2}))?", name, re.I)
    if m:
        delta = timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0))
        return timezone(delta if m.group(1) == "+" else -delta)
    alias = {"москва": "Europe/Moscow", "мск": "Europe/Moscow", "msk": "Europe/Moscow", "лондон": "Europe/London", "нью-йорк": "America/New_York",
             "екатеринбург": "Asia/Yekaterinburg", "новосибирск": "Asia/Novosibirsk", "владивосток": "Asia/Vladivostok", "калининград": "Europe/Kaliningrad",
             "самара": "Europe/Samara", "омск": "Asia/Omsk", "красноярск": "Asia/Krasnoyarsk", "иркутск": "Asia/Irkutsk", "якутск": "Asia/Yakutsk",
             "магадан": "Asia/Magadan", "камчатка": "Asia/Kamchatka", "берлин": "Europe/Berlin", "париж": "Europe/Paris", "токио": "Asia/Tokyo",
             "пекин": "Asia/Shanghai", "дубай": "Asia/Dubai", "стамбул": "Europe/Istanbul", "минск": "Europe/Minsk", "киев": "Europe/Kyiv",
             "алматы": "Asia/Almaty", "ташкент": "Asia/Tashkent", "лос-анджелес": "America/Los_Angeles", "сидней": "Australia/Sydney",
             "pst": "America/Los_Angeles", "est": "America/New_York", "cet": "Europe/Paris", "jst": "Asia/Tokyo"}.get(name.lower(), name)
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(alias)
    except Exception:  # noqa: BLE001 - no tz database (Windows without tzdata): fixed offsets for common zones
        fixed = {"Europe/Moscow": 3, "Europe/Kaliningrad": 2, "Europe/Samara": 4, "Asia/Yekaterinburg": 5, "Asia/Omsk": 6, "Asia/Novosibirsk": 7,
                 "Asia/Krasnoyarsk": 7, "Asia/Irkutsk": 8, "Asia/Yakutsk": 9, "Asia/Vladivostok": 10, "Asia/Magadan": 11, "Asia/Kamchatka": 12,
                 "Europe/Minsk": 3, "Europe/Istanbul": 3, "Asia/Dubai": 4, "Asia/Tashkent": 5, "Asia/Almaty": 5, "Asia/Shanghai": 8, "Asia/Tokyo": 9}
        if alias in fixed:
            return timezone(timedelta(hours=fixed[alias]), alias)
        raise UtilError(f"Не знаю часовой пояс «{name}». Примеры: Europe/Moscow, UTC+3, Asia/Tokyo",
                        f"Unknown time zone “{name}”. Examples: Europe/Moscow, UTC+3, Asia/Tokyo")

def parse_moment(text: str, tz) -> datetime:
    text = text.strip()
    if re.fullmatch(r"-?\d{9,13}(?:\.\d+)?", text):
        value = float(text)
        if abs(value) > 1e11:
            value /= 1000
        return datetime.fromtimestamp(value, timezone.utc)
    if text.lower() in ("now", "сейчас", ""):
        return datetime.now(timezone.utc)
    fmts = ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y", "%d/%m/%Y %H:%M", "%d/%m/%Y",
            "%H:%M", "%H:%M:%S", "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S GMT")
    clean = text.replace("Z", "+0000") if text.endswith("Z") else text
    for fmt in fmts:
        try:
            dt = datetime.strptime(clean, fmt)
        except ValueError:
            continue
        if fmt in ("%H:%M", "%H:%M:%S"):
            today = datetime.now(tz)
            dt = dt.replace(year=today.year, month=today.month, day=today.day)
        if fmt.endswith("GMT"):
            dt = dt.replace(tzinfo=timezone.utc)
        return dt if dt.tzinfo else dt.replace(tzinfo=tz)
    raise UtilError("Не понимаю дату. Примеры: 1700000000, 2025-03-08 14:30, 08.03.2025", "Cannot read the date. Examples: 1700000000, 2025-03-08 14:30")

@utility("conv.time", "convert", "Unix-время ↔ дата", "Unix time ↔ date",
         "Timestamp (секунды и миллисекунды) ↔ ISO 8601, RFC 2822, локальное время, день недели, номер недели.",
         "Timestamps (seconds and milliseconds) ↔ ISO 8601, RFC 2822, local time, weekday, week number.",
         opts=[O("tz", "text", "Часовой пояс для ввода/вывода", "Time zone", "local", hint="local, UTC, Europe/Moscow, UTC+3, Москва", hint_en="local, UTC, Europe/Moscow, UTC+3")],
         live=True, ai_default=True, placeholder="1735689600", keys="unix timestamp время дата epoch iso конвертер")
def conv_time(ctx):
    tz = get_zone(ctx.o["tz"])
    dt = parse_moment(ctx.text or "now", tz)
    local = dt.astimezone(tz)
    utc = dt.astimezone(timezone.utc)
    wd = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    now = datetime.now(timezone.utc)
    diff = (utc - now).total_seconds()
    rel = _relative(diff, ctx.en)
    from email.utils import format_datetime
    return {"info": [["Unix (s)", int(utc.timestamp())], ["Unix (ms)", int(utc.timestamp() * 1000)], ["ISO 8601", local.isoformat(timespec="seconds")],
                     ["UTC", utc.strftime("%Y-%m-%d %H:%M:%S")], [ctx.tr("В поясе", "In zone") + f" ({local.tzname()})", local.strftime("%d.%m.%Y %H:%M:%S")],
                     ["RFC 2822", format_datetime(utc)], [ctx.tr("День недели", "Weekday"), local.strftime("%A") if ctx.en else wd[local.weekday()]],
                     [ctx.tr("Неделя ISO", "ISO week"), local.isocalendar()[1]], [ctx.tr("День года", "Day of year"), local.timetuple().tm_yday],
                     [ctx.tr("Относительно сейчас", "Relative to now"), rel]]}

def _relative(seconds: float, en: bool) -> str:
    future, s = seconds > 0, abs(seconds)
    for size, ru, e in ((31556952, "г.", "years"), (2629746, "мес.", "months"), (86400, "дн.", "days"), (3600, "ч", "hours"), (60, "мин", "minutes")):
        if s >= size:
            n = s / size
            txt = f"{n:.1f} {e if en else ru}"
            return (f"in {txt}" if future else f"{txt} ago") if en else (f"через {txt}" if future else f"{txt} назад")
    return "now" if en else "сейчас"

@utility("conv.tz", "convert", "Часовые пояса: который час там", "Time zones: what time is it there",
         "Переводит время между поясами и показывает его сразу в нескольких городах (удобно для созвонов).",
         "Converts a time between zones and shows it in several cities at once (handy for calls).",
         opts=[O("from", "text", "Из пояса", "From zone", "local"),
               O("zones", "textarea", "Показать в поясах (по строке)", "Show in zones (one per line)", "Europe/Moscow\nEurope/London\nAmerica/New_York\nAsia/Tokyo\nUTC")],
         live=True, ai_default=True, placeholder="15:00", keys="часовой пояс время город созвон timezone world clock")
def conv_tz(ctx):
    src = get_zone(ctx.o["from"])
    dt = parse_moment(ctx.text or "now", src)
    rows = []
    for name in lines_of(ctx.o["zones"]):
        if not name.strip():
            continue
        z = get_zone(name)
        t = dt.astimezone(z)
        off = t.utcoffset() or timedelta(0)
        sign = "+" if off >= timedelta(0) else "-"
        hours = abs(off)
        rows.append([name.strip(), t.strftime("%H:%M"), t.strftime("%d.%m.%Y"), f"UTC{sign}{int(hours.total_seconds() // 3600)}:{int(hours.total_seconds() % 3600 // 60):02d}"])
    return {"table": table([ctx.tr("Пояс", "Zone"), ctx.tr("Время", "Time"), ctx.tr("Дата", "Date"), ctx.tr("Смещение", "Offset")], rows)}

# ── color ───────────────────────────────────────────────────────────────────────────────────────────────────────────
@utility("conv.color", "convert", "Конвертер цветов", "Color converter",
         "HEX ↔ RGB ↔ HSL ↔ HSV ↔ CMYK, ближайшее название, контраст с белым/чёрным по WCAG и образец.",
         "HEX ↔ RGB ↔ HSL ↔ HSV ↔ CMYK, nearest name, WCAG contrast against white/black and a swatch.",
         live=True, ai_default=True, placeholder="#ff6b35", keys="цвет hex rgb hsl cmyk color конвертер контраст")
def conv_color(ctx):
    from . import colors
    raw = ctx.need_text("Введите цвет: #ff6b35, rgb(255,107,53), hsl(…) или название", "Enter a color: #ff6b35, rgb(255,107,53), hsl(…) or a name").strip()
    try:
        rgb = colors.parse(raw)
    except ValueError:
        raise UtilError(f"Не понимаю цвет «{raw}»", f"Cannot read the color “{raw}”")
    h, s, l = colors.to_hsl(rgb)
    hv, sv, vv = colors.to_hsv(rgb)
    c, m, y, k = colors.to_cmyk(rgb)
    hexv = colors.to_hex(rgb)
    cw, cb = colors.contrast(rgb, (255, 255, 255)), colors.contrast(rgb, (0, 0, 0))
    grade = lambda r: "AAA" if r >= 7 else "AA" if r >= 4.5 else "AA (крупный)" if r >= 3 and not ctx.en else "AA large" if r >= 3 else "—"  # noqa: E731
    swatch = _swatch_png([rgb])
    ctx.out_bytes(f"color-{hexv[1:]}.png", swatch)
    return {"info": [["HEX", hexv], ["RGB", f"rgb({rgb[0]}, {rgb[1]}, {rgb[2]})"], ["HSL", f"hsl({h:.0f}, {s:.0f}%, {l:.0f}%)"],
                     ["HSV", f"hsv({hv:.0f}, {sv:.0f}%, {vv:.0f}%)"], ["CMYK", f"{c:.0f}% {m:.0f}% {y:.0f}% {k:.0f}%"],
                     ["Android/Flutter", "0xFF" + hexv[1:].upper()], [ctx.tr("Похож на", "Close to"), colors.nearest_name(rgb)],
                     [ctx.tr("Контраст с белым", "Contrast vs white"), f"{cw:.2f}:1 {grade(cw)}"], [ctx.tr("Контраст с чёрным", "Contrast vs black"), f"{cb:.2f}:1 {grade(cb)}"]],
            "swatches": [hexv]}

def _swatch_png(colors_list, size: int = 96) -> bytes:
    from PIL import Image
    img = Image.new("RGB", (size * len(colors_list), size))
    for i, rgb in enumerate(colors_list):
        img.paste(tuple(rgb[:3]), (i * size, 0, (i + 1) * size, size))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()

# ── roman / dates / css / aspect / percent ──────────────────────────────────────────────────────────────────────────
_ROMAN = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]

@utility("conv.roman", "convert", "Римские цифры", "Roman numerals",
         "2025 ↔ MMXXV, от 1 до 3999.", "2025 ↔ MMXXV, from 1 to 3999.", live=True, keys="римские цифры roman numerals")
def conv_roman(ctx):
    out = []
    for line in lines_of(ctx.need_text()):
        v = line.strip().upper()
        if not v:
            continue
        if v.isdigit():
            n = int(v)
            if not 0 < n < 4000:
                raise UtilError("Римскими цифрами пишут числа от 1 до 3999", "Roman numerals cover 1 to 3999")
            s = ""
            for value, sym in _ROMAN:
                while n >= value:
                    s += sym
                    n -= value
            out.append(s)
        elif re.fullmatch(r"[MDCLXVI]+", v):
            total, prev = 0, 0
            for ch in reversed(v):
                value = dict((s, n) for n, s in _ROMAN if len(s) == 1)[ch]
                total += -value if value < prev else value
                prev = max(prev, value)
            out.append(str(total))
        else:
            raise UtilError(f"«{line}» — не число и не римская запись", f"“{line}” is neither a number nor a Roman numeral")
    return "\n".join(out)

def _parse_date(text: str) -> date:
    text = text.strip()
    if text.lower() in ("", "сегодня", "today", "now", "сейчас"):
        return date.today()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise UtilError(f"Не понимаю дату «{text}» — нужно 2025-03-08 или 08.03.2025", f"Cannot read the date “{text}” — use 2025-03-08")

@utility("conv.date", "convert", "Калькулятор дат", "Date calculator",
         "Сколько дней между датами (и рабочих дней), какая дата будет через N дней, день недели, возраст.",
         "Days between dates (and working days), the date N days later, weekday, age.",
         input="text2", label2="Вторая дата (или пусто)", label2_en="Second date (or empty)",
         opts=[O("add", "int", "Прибавить дней к первой дате", "Add days to the first date", 0, min=-100000, max=100000)],
         live=True, ai_default=True, placeholder="08.03.2025", keys="даты дни между датами рабочие дни возраст date calculator")
def conv_date(ctx):
    a = _parse_date(ctx.text or "")
    wd = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    name = lambda d: d.strftime("%A") if ctx.en else wd[d.weekday()]  # noqa: E731
    info = [[ctx.tr("Дата", "Date"), f"{a:%d.%m.%Y}, {name(a)}"], [ctx.tr("Неделя ISO", "ISO week"), a.isocalendar()[1]]]
    if int(ctx.o["add"]):
        b = a + timedelta(days=int(ctx.o["add"]))
        info.append([ctx.tr(f"+{ctx.o['add']} дн.", f"+{ctx.o['add']} days"), f"{b:%d.%m.%Y}, {name(b)}"])
    if (ctx.text2 or "").strip():
        b = _parse_date(ctx.text2)
        lo, hi = sorted((a, b))
        days = (hi - lo).days
        work = sum(1 for i in range(days + 1) if (lo + timedelta(days=i)).weekday() < 5)
        years = hi.year - lo.year - ((hi.month, hi.day) < (lo.month, lo.day))
        info += [[ctx.tr("Между датами", "Between"), ctx.tr(f"{days} дн. (~{days / 7:.1f} нед.)", f"{days} days (~{days / 7:.1f} weeks)")],
                 [ctx.tr("Рабочих дней (пн–пт, включительно)", "Working days (Mon–Fri, inclusive)"), work],
                 [ctx.tr("Полных лет", "Full years"), years]]
    return {"info": info}

@utility("conv.css", "convert", "px ↔ rem/em/pt/vw", "px ↔ rem/em/pt/vw",
         "Пересчёт CSS-единиц при заданном базовом шрифте и ширине экрана.", "Converts CSS units for a base font size and viewport.",
         opts=[O("base", "number", "Базовый шрифт, px", "Base font, px", 16, min=1, max=100), O("vw", "number", "Ширина экрана, px", "Viewport width, px", 1440, min=100, max=10000)],
         live=True, placeholder="24px", keys="css px rem em pt vw единицы")
def conv_css(ctx):
    base, vw = float(ctx.o["base"]), float(ctx.o["vw"])
    rows = []
    for line in lines_of(ctx.need_text("Введите значение: 24px, 1.5rem, 12pt", "Enter a value: 24px, 1.5rem, 12pt")):
        m = re.fullmatch(r"\s*([-\d.,]+)\s*(px|rem|em|pt|vw|%|pc|cm|mm|in)?\s*", line)
        if not m:
            continue
        v, unit = as_float(m.group(1)), (m.group(2) or "px")
        px = {"px": v, "rem": v * base, "em": v * base, "pt": v * 96 / 72, "vw": v * vw / 100, "%": v * base / 100, "pc": v * 16,
              "cm": v * 96 / 2.54, "mm": v * 96 / 25.4, "in": v * 96}[unit]
        rows.append([line.strip(), f"{px:.2f}px", f"{px / base:.4g}rem", f"{px * 72 / 96:.4g}pt", f"{px / vw * 100:.4g}vw"])
    return {"table": table([ctx.tr("Значение", "Value"), "px", "rem/em", "pt", "vw"], rows)}

@utility("conv.aspect", "convert", "Соотношение сторон", "Aspect ratio",
         "1920×1080 → 16:9; новая высота для другой ширины с сохранением пропорций; стандартные форматы.",
         "1920×1080 → 16:9; the new height for another width keeping proportions; common formats.",
         opts=[O("width", "int", "Новая ширина (необязательно)", "New width (optional)", 0, min=0, max=100000)],
         live=True, placeholder="1920x1080", keys="соотношение сторон пропорции 16:9 aspect ratio разрешение")
def conv_aspect(ctx):
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*[x×:*/ ]\s*(\d+(?:[.,]\d+)?)", ctx.need_text("Введите размер: 1920x1080 или 16:9", "Enter a size: 1920x1080 or 16:9"))
    if not m:
        raise UtilError("Нужно два числа: 1920x1080", "Need two numbers: 1920x1080")
    w, h = as_float(m.group(1)), as_float(m.group(2))
    if not w or not h:
        raise UtilError("Стороны должны быть больше нуля", "Sides must be greater than zero")
    g = math.gcd(int(w), int(h)) if w.is_integer() and h.is_integer() else 1
    ratio = w / h
    named = min(((16, 9), (4, 3), (3, 2), (21, 9), (1, 1), (5, 4), (9, 16), (2, 1), (16, 10), (3, 4), (4, 5), (2, 3)), key=lambda p: abs(p[0] / p[1] - ratio))
    info = [[ctx.tr("Пропорция", "Ratio"), f"{int(w // g)}:{int(h // g)}" if g > 1 else f"{ratio:.4f}:1"], [ctx.tr("Десятичная", "Decimal"), f"{ratio:.4f}"],
            [ctx.tr("Ближайший стандарт", "Closest standard"), f"{named[0]}:{named[1]}"]]
    if int(ctx.o["width"]):
        nw = int(ctx.o["width"])
        info.append([ctx.tr("Новый размер", "New size"), f"{nw}×{round(nw / ratio)}"])
    sizes = [(640, "SD"), (1280, "HD"), (1920, "Full HD"), (2560, "QHD"), (3840, "4K")]
    for sw, name in sizes:
        info.append([name, f"{sw}×{round(sw / ratio)}"])
    return {"info": info}

@utility("conv.percent", "convert", "Проценты", "Percentages",
         "X% от Y, сколько процентов X от Y, изменение A → B в %, НДС/скидка, сложный процент.",
         "X% of Y, X as a percent of Y, change A → B in %, VAT/discount, compound interest.",
         opts=[O("mode", "choice", "Задача", "Task", "of", choices=[("of", "X% от Y", "X% of Y"), ("share", "X — это сколько % от Y", "X is what % of Y"),
                                                              ("change", "Изменение от A к B", "Change from A to B"), ("add", "Y + X% (наценка, НДС)", "Y + X% (markup, VAT)"),
                                                              ("sub", "Y − X% (скидка)", "Y − X% (discount)"), ("vat_out", "Выделить X% НДС из суммы Y", "Extract X% VAT from Y"),
                                                              ("compound", "Сложный %: Y под X% годовых на N лет", "Compound: Y at X% for N years")]),
               O("x", "number", "X (проценты или A)", "X (percent or A)", 20), O("y", "number", "Y (число или B)", "Y (number or B)", 1000),
               O("n", "number", "N (лет)", "N (years)", 5, show="mode=compound")],
         input="none", live=True, ai_default=True, keys="проценты процент от числа скидка ндс наценка percent vat discount")
def conv_percent(ctx):
    x, y, mode = float(ctx.o["x"]), float(ctx.o["y"]), ctx.o["mode"]
    if mode == "of":
        return _fmt_num(x * y / 100, ctx)
    if mode == "share":
        if not y:
            raise UtilError("Y не может быть нулём", "Y cannot be zero")
        return f"{_fmt_num(x / y * 100, ctx)}%"
    if mode == "change":
        if not x:
            raise UtilError("A не может быть нулём", "A cannot be zero")
        return f"{'+' if y >= x else ''}{_fmt_num((y - x) / abs(x) * 100, ctx)}%"
    if mode == "add":
        return {"info": [[ctx.tr("Итого", "Total"), _fmt_num(y * (1 + x / 100), ctx)], [ctx.tr("Добавлено", "Added"), _fmt_num(y * x / 100, ctx)]]}
    if mode == "sub":
        return {"info": [[ctx.tr("Итого", "Total"), _fmt_num(y * (1 - x / 100), ctx)], [ctx.tr("Скидка", "Discount"), _fmt_num(y * x / 100, ctx)]]}
    if mode == "vat_out":
        vat = y * x / (100 + x)
        return {"info": [[ctx.tr("НДС", "VAT"), _fmt_num(round(vat, 2), ctx)], [ctx.tr("Без НДС", "Net"), _fmt_num(round(y - vat, 2), ctx)]]}
    n = float(ctx.o["n"])
    rows = [[int(i), _fmt_num(round(y * (1 + x / 100) ** i, 2), ctx)] for i in range(0, int(n) + 1)]
    return {"table": table([ctx.tr("Год", "Year"), ctx.tr("Сумма", "Amount")], rows)}
