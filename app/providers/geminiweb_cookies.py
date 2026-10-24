"""Импорт кук Google для провайдера Gemini Web (раунд 37).

Принимает всё, что пользователь реально может скопировать из браузера, и объясняет,
что именно не так, если импорт не удался:

* строка ``Cookie:`` из DevTools → Network (с префиксом или без) и «Copy as cURL»;
* таблица DevTools → Application → Cookies, скопированная как текст (колонки через Tab);
* JSON-экспорт Cookie-Editor / EditThisCookie / Playwright storage state, словарь name→value,
  старый формат ``{"cookie": "…"}``;
* Netscape ``cookies.txt`` (в т. ч. строки ``#HttpOnly_…``);
* только два значения: ``__Secure-1PSID`` и ``__Secure-1PSIDTS`` — парами «имя значение»
  или просто двумя строками значений (их узнаём по префиксам ``g.a0`` и ``sidts-``).

Результат — нормализованная строка ``name=value; …`` только с куками google.com.
"""
from __future__ import annotations

import json
import re
from http.cookies import Morsel
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

PSID = "__Secure-1PSID"
PSIDTS = "__Secure-1PSIDTS"
# Куки google.com, которые браузер шлёт на gemini.google.com (порядок — для красивой строки Cookie;
# из окна входа берём только их). Чужие домены (youtube.com, accounts.google.com, google.ru) отбрасываются.
KNOWN = (
    PSID, PSIDTS, "__Secure-1PSIDCC", "__Secure-3PSID", "__Secure-3PSIDTS", "__Secure-3PSIDCC",
    "SID", "HSID", "SSID", "APISID", "SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID",
    "SIDCC", "NID", "AEC", "SOCS", "__Secure-ENID", "COMPASS",
)
_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_ROW_SPLIT_RE = re.compile(r"\t|\s{2,}")


@dataclass
class CookieImport:
    cookies: Dict[str, str] = field(default_factory=dict)
    format: str = ""
    errors: List[Dict[str, str]] = field(default_factory=list)
    warnings: List[Dict[str, str]] = field(default_factory=list)
    dropped: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and PSID in self.cookies

    @property
    def header(self) -> str:
        return cookie_header(self.cookies)

    def add(self, level: str, code: str, ru: str, en: str) -> None:
        (self.errors if level == "error" else self.warnings).append({"code": code, "ru": ru, "en": en})

    def public(self) -> Dict[str, Any]:
        return {
            "ok": self.ok, "format": self.format, "names": sorted(self.cookies),
            "has_psid": PSID in self.cookies, "has_psidts": PSIDTS in self.cookies,
            "errors": self.errors, "warnings": self.warnings, "dropped": self.dropped[:20],
            "cookie": self.header if self.ok else "",
        }


def cookie_header(cookies: Dict[str, str]) -> str:
    order = {name: i for i, name in enumerate(KNOWN)}
    return "; ".join(f"{k}={v}" for k, v in sorted(cookies.items(), key=lambda kv: (order.get(kv[0], 99), kv[0])))


def parse_header(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for part in re.split(r";\s*", text or ""):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name, value = name.strip(), value.strip().strip('"')
        if name and _NAME_RE.match(name):
            out[name] = value
    return out


def set_cookie_value(header: str, name: str, value: str) -> str:
    """Подменяет одно значение в строке Cookie (для ротации __Secure-1PSIDTS)."""
    cookies = parse_header(header)
    cookies[name] = value
    return cookie_header(cookies)


# ------------------------------------------------------------------ разбор форматов


def _strip_wrappers(text: str) -> str:
    text = text.strip().replace("\r\n", "\n")
    text = re.sub(r"(?:\\|\^)\n", " ", text).replace('^"', '"')
    curl = re.search(r"(?:-H|--header)\s+(['\"])cookie\s*:\s*(.*?)\1", text, re.I | re.S)
    if curl:
        return curl[2].strip()
    curl = re.search(r"(?:-b|--cookie)\s+(['\"])(.*?)\1", text, re.I | re.S)
    if curl:
        return curl[2].strip()
    line = re.search(r"(?:^|\n)\s*cookie\s*:\s*([^\n]+)", text, re.I)
    if line and "\t" not in text:
        return line[1].strip()
    return text


Row = Tuple[str, str, str, Optional[float]]   # name, value, domain, expires (unix) | None


def _expiry(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number / 1000 if number > 1e11 else number


def _json_rows(data: Any) -> Optional[List[Row]]:
    if isinstance(data, dict) and isinstance(data.get("cookies"), list):
        data = data["cookies"]
    if isinstance(data, dict):
        if isinstance(data.get("cookie"), str):
            return [(k, v, "", None) for k, v in parse_header(data["cookie"]).items()]
        if data and all(isinstance(v, str) for v in data.values()):
            return [(str(k), v, "", None) for k, v in data.items()]
        return None
    if not isinstance(data, list):
        return None
    rows: List[Row] = []
    for item in data:
        if not isinstance(item, dict) or not item.get("name") or item.get("value") is None:
            continue
        expires = item.get("expirationDate", item.get("expires", item.get("expiry")))
        if item.get("session") is True:
            expires = None
        rows.append((str(item["name"]), str(item["value"]), str(item.get("domain") or item.get("host") or ""),
                     _expiry(expires)))
    return rows


def _netscape_rows(lines: List[str]) -> Optional[List[Row]]:
    rows: List[Row] = []
    for raw in lines:
        line = raw.strip()
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7 or parts[1].upper() not in ("TRUE", "FALSE") or parts[3].upper() not in ("TRUE", "FALSE"):
            return None
        rows.append((parts[5].strip(), parts[6].strip(), parts[0].strip(), _expiry(parts[4])))
    return rows or None


def _devtools_rows(lines: List[str]) -> Optional[List[Row]]:
    """Application → Cookies: Name, Value, Domain, Path, Expires, Size, HttpOnly, … (через Tab)."""
    rows: List[Row] = []
    for raw in lines:
        if not raw.strip():
            continue
        parts = [p.strip() for p in raw.split("\t")]
        if len(parts) < 3:
            return None
        if parts[0].lower() in ("name", "имя") and parts[1].lower() in ("value", "значение"):
            continue
        name, value, domain = parts[0], parts[1], parts[2]
        if not _NAME_RE.match(name):
            return None
        expires: Optional[float] = None
        if len(parts) > 4 and re.match(r"^\d{4}-\d\d-\d\d", parts[4]):
            try:
                expires = time.mktime(time.strptime(parts[4][:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
            except ValueError:
                expires = None
        rows.append((name, value, domain if ("." in domain or domain.startswith("/")) else "", expires))
    return rows or None


def _pair_rows(lines: List[str]) -> Optional[List[Row]]:
    """«имя значение» построчно (Tab, «:», «=», пробелы) или голые значения g.a0… / sidts-…."""
    rows: List[Row] = []
    for raw in lines:
        line = raw.strip().rstrip(";")
        if not line:
            continue
        match = re.match(r"^([!#$%&'*+.^_`|~0-9A-Za-z-]+)\s*(?:\t|=|:|\s)\s*(\S+)$", line)
        if match and not line.startswith(("g.a0", "sidts-")):
            rows.append((match[1], match[2].strip('"'), "", None))
        elif re.match(r"^g\.a0\S{20,}$", line):
            rows.append((PSID, line, "", None))
        elif re.match(r"^sidts-\S{10,}$", line):
            rows.append((PSIDTS, line, "", None))
        else:
            return None
    return rows or None


def _domain_ok(domain: str) -> bool:
    d = domain.lower().lstrip(".")
    return not d or d == "google.com" or d == "gemini.google.com"


def parse_cookie_import(raw: Any) -> CookieImport:
    result = CookieImport()
    text = str(raw or "").strip()
    if not text:
        result.add("error", "empty", "Поле пустое — вставьте куки.", "The field is empty — paste the cookies.")
        return result
    if len(text) > 512_000:
        result.add("error", "too_big", "Слишком большой текст (больше 500 КБ).", "The text is too large (over 500 KB).")
        return result
    text = _strip_wrappers(text)
    rows: Optional[List[Row]] = None
    if text[:1] in "[{":
        try:
            rows = _json_rows(json.loads(text))
            result.format = "json"
        except ValueError:
            rows = None
    lines = text.split("\n")
    if rows is None and "\t" in text:
        rows = _netscape_rows(lines)
        result.format = "netscape" if rows else result.format
        if rows is None:
            rows = _devtools_rows(lines)
            result.format = "devtools-table" if rows else result.format
    if rows is None and ("=" in text and (";" in text or len(lines) == 1)) and not re.match(r"^\S+\t", text):
        rows = [(k, v, "", None) for k, v in parse_header(" ".join(text.split())).items()]
        result.format = "header" if rows else ""
    if not rows:
        rows = _pair_rows(lines)
        result.format = "pairs" if rows else ""
    if not rows:
        result.add("error", "unknown_format",
                   "Не удалось распознать формат. Вставьте строку Cookie из Network, таблицу из Application → Cookies, "
                   "JSON из Cookie-Editor или два значения __Secure-1PSID и __Secure-1PSIDTS.",
                   "Unknown format. Paste the Cookie header from Network, the Application → Cookies table, "
                   "a Cookie-Editor JSON export, or the two values __Secure-1PSID and __Secure-1PSIDTS.")
        return result
    _collect(result, rows)
    _validate(result)
    return result


def _collect(result: CookieImport, rows: Iterable[Row]) -> None:
    now = time.time()
    priority: Dict[str, int] = {}
    foreign: Dict[str, str] = {}
    expired: List[str] = []
    for name, value, domain, expires in rows:
        if not _NAME_RE.match(name or ""):
            continue
        if not _domain_ok(domain):
            foreign.setdefault(name, domain)
            result.dropped.append(f"{name} ({domain})")
            continue
        rank = 2 if domain.lower() == ".google.com" else 1 if domain else 0
        if name in result.cookies and priority.get(name, -1) > rank:
            continue
        if expires and expires < now:
            expired.append(name)
        result.cookies[name] = value
        priority[name] = rank
        foreign.pop(name, None)
    for name in (PSID, PSIDTS):
        if name not in result.cookies and name in foreign:
            domain = foreign[name]
            result.add("error", "wrong_domain",
                       f"Кука {name} взята с домена {domain}, а нужна с .google.com. В Application → Cookies "
                       "выберите строку https://gemini.google.com и копируйте куки с доменом .google.com.",
                       f"{name} comes from {domain}, but it must be the .google.com one. In Application → Cookies "
                       "pick https://gemini.google.com and copy the cookies whose domain is .google.com.")
    for name in expired:
        if name in (PSID, PSIDTS):
            result.add("error", "expired_local",
                       f"Кука {name} уже просрочена (по дате в экспорте). Обновите страницу Gemini и скопируйте заново.",
                       f"{name} has already expired (by the export date). Reload Gemini and copy it again.")


def _validate(result: CookieImport) -> None:
    for name, value in list(result.cookies.items()):
        if "…" in value or value.endswith("..."):
            result.add("error", "truncated",
                       f"Значение {name} обрезано («…»). Копируйте через «Copy value» или двойным щелчком по значению.",
                       f"The {name} value is truncated (\"…\"). Use Copy value or double-click the value.")
        elif re.search(r"[\s\x00-\x1f\x7f-\uffff]", value):
            result.add("error", "bad_value",
                       f"В значении {name} есть пробелы или лишние символы — скопируйте его заново.",
                       f"The {name} value contains spaces or stray characters — copy it again.")
    if PSID not in result.cookies:
        if not any(e["code"] == "wrong_domain" for e in result.errors):
            result.add("error", "no_1psid",
                       "Нет куки __Secure-1PSID — без неё Google не узнает аккаунт. Проверьте, что вы вошли в "
                       "gemini.google.com, и копируйте куки именно оттуда.",
                       "__Secure-1PSID is missing — Google cannot identify the account without it. Make sure you "
                       "are signed in to gemini.google.com and copy the cookies from there.")
    elif not result.cookies[PSID].startswith("g.a0"):
        result.add("warning", "odd_1psid",
                   "__Secure-1PSID обычно начинается с «g.a0». Убедитесь, что скопировано значение, а не другая колонка.",
                   "__Secure-1PSID usually starts with \"g.a0\". Make sure you copied the value, not another column.")
    if PSID in result.cookies and PSIDTS not in result.cookies:
        result.add("warning", "no_1psidts",
                   "Нет __Secure-1PSIDTS. С ней вход держится надёжнее: добавьте её (в Application → Cookies "
                   "она рядом с __Secure-1PSID).",
                   "__Secure-1PSIDTS is missing. Sign-in is more reliable with it: add it (in Application → Cookies "
                   "it sits next to __Secure-1PSID).")


def normalize_cookie_field(raw: Any) -> str:
    """Для хранения: нормализованная строка, если импорт удался, иначе исходный текст как есть."""
    parsed = parse_cookie_import(raw)
    return parsed.header if parsed.cookies and not parsed.errors else str(raw or "").strip()


def cookies_from_webview(items: Iterable[Any]) -> Dict[str, str]:
    """Куки из pywebview ``window.get_cookies()`` (список SimpleCookie) → {name: value} для .google.com."""
    out: Dict[str, str] = {}
    for item in items or []:
        if isinstance(item, Morsel) or (isinstance(item, dict) and "name" in item):
            morsels: Iterable[Any] = [item]
        else:
            morsels = item.values() if hasattr(item, "values") else [item]
        for morsel in morsels:
            if isinstance(morsel, dict) and not isinstance(morsel, Morsel):
                name, value, domain = morsel.get("name"), morsel.get("value"), morsel.get("domain", "")
            else:
                name, value = getattr(morsel, "key", ""), getattr(morsel, "value", "")
                domain = morsel.get("domain", "") if hasattr(morsel, "get") else ""
            if name and value is not None and _domain_ok(str(domain or "")) and (name in KNOWN or str(name).startswith("__Secure-")):
                out[str(name)] = str(value)
    return out
