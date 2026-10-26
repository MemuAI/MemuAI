"""Encrypted secret vault for Do For You (DFY).

Secrets (logins, payment cards and personal data: phone, e-mail, name, addresses,
documents, company details; one-time codes only in memory) are stored encrypted:

* Windows — DPAPI (``CryptProtectData``) bound to the current Windows user; no
  key ever touches the disk.
* other OS — a fallback: a random 256-bit key in ``dfy_vault.key`` (mode 600)
  and an HMAC-SHA256 stream cipher with an encrypt-then-MAC tag.  It protects
  against casual reading of the vault file, not against a process running as
  the same user; ``scheme`` in :meth:`SecretVault.info` says so explicitly.

The DFY model never sees a value.  It sees entries as
``{"id", "kind", "label", "domain", "mask", "fields"}`` and writes placeholders
``{{secret:ID.field}}`` that :meth:`SecretVault.resolve` replaces only inside
the browser input call.  A ``login`` secret is bound to its domain: it is
released only on a page whose host is that domain or its subdomain.  Read-only
derived fields (``phone_national``, ``birth_date_dmy``, ``full_name``, ``exp``,
``code_1``…) give other formats of a stored value without revealing it.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets as _secrets
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

KINDS = ("login", "card", "identity", "phone", "email", "address", "document", "company", "otp", "text")
KIND_FIELDS = {
    "login": ("username", "password"),
    "card": ("number", "exp_month", "exp_year", "cvc", "holder"),
    "identity": ("first_name", "last_name", "middle_name", "email", "phone", "birth_date",
                 "address", "city", "postal_code", "country"),
    "phone": ("phone",),
    "email": ("email",),
    "address": ("recipient", "country", "region", "city", "street", "house", "apartment", "postal_code",
                "address_type"),
    "document": ("doc_type", "series", "number", "issued_by", "issue_date", "issuer_code", "expiry_date",
                 "birth_place"),
    "company": ("company_name", "inn", "kpp", "ogrn", "legal_address"),
    "otp": ("code",),
    "text": ("value",),
}

REQUESTABLE_KINDS = tuple(kind for kind in KINDS if kind != "otp")
ADDRESS_TYPES = ("shipping", "billing", "both")

NOT_REDACTED = frozenset(("exp_month", "exp_year", "country", "region", "city", "address_type", "doc_type"))
PLACEHOLDER_RE = re.compile(r"\{\{\s*secret:([A-Za-z0-9_-]{4,40})\.([a-z0-9_]{1,32})\s*\}\}")
_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")

_CALLING_CODES = frozenset("""
1 7 20 27 30 31 32 33 34 36 39 40 41 43 44 45 46 47 48 49 51 52 53 54 55 56 57 58 60 61 62 63 64 65 66 81 82 84
86 90 91 92 93 94 95 98 211 212 213 216 218 220 221 222 223 224 225 226 227 228 229 230 231 232 233 234 235 236
237 238 239 240 241 242 243 244 245 246 248 249 250 251 252 253 254 255 256 257 258 260 261 262 263 264 265 266
267 268 269 290 291 297 298 299 350 351 352 353 354 355 356 357 358 359 370 371 372 373 374 375 376 377 378 380
381 382 383 385 386 387 389 420 421 423 500 501 502 503 504 505 506 507 508 509 590 591 592 593 594 595 596 597
598 599 670 672 673 674 675 676 677 678 679 680 681 682 683 685 686 687 688 689 690 691 692 850 852 853 855 856
880 886 960 961 962 963 964 965 966 967 968 970 971 972 973 974 975 976 977 992 993 994 995 996 998""".split())

class VaultError(ValueError):
    """A secret cannot be stored or used (invalid value, missing, wrong domain, wrong field).

    A ``ValueError``: callers of ``DFYManager.answer`` (API, Telegram) report it as bad input."""

def _dpapi(data: bytes, *, protect: bool) -> bytes:  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    source = ctypes.create_string_buffer(data, len(data))
    source_blob = DataBlob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    entropy_raw = b"MemuAI-DFY-vault-v1"
    entropy = ctypes.create_string_buffer(entropy_raw, len(entropy_raw))
    entropy_blob = DataBlob(len(entropy_raw), ctypes.cast(entropy, ctypes.POINTER(ctypes.c_ubyte)))
    result_blob = DataBlob()
    crypt32 = ctypes.windll.crypt32
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData

    ok = function(ctypes.byref(source_blob), None, ctypes.byref(entropy_blob), None, None, 0x1,
                  ctypes.byref(result_blob))
    if not ok:
        raise ctypes.WinError(ctypes.windll.kernel32.GetLastError())
    try:
        return ctypes.string_at(result_blob.pbData, result_blob.cbData)
    finally:
        kernel32 = ctypes.windll.kernel32
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(ctypes.cast(result_blob.pbData, ctypes.c_void_p))

def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(key, b"enc" + nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])

class _FileKeyCipher:
    """Fallback cipher: HMAC-SHA256-CTR + HMAC-SHA256 tag (encrypt-then-MAC)."""

    def __init__(self, key_path: Path) -> None:
        self.key_path = key_path
        self._key: Optional[bytes] = None

    def _load_key(self) -> bytes:
        if self._key:
            return self._key
        if self.key_path.is_file():
            raw = base64.b64decode(self.key_path.read_text("ascii").strip())
            if len(raw) != 32:
                raise VaultError("Ключ хранилища DFY повреждён")
        else:
            raw = _secrets.token_bytes(32)
            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self.key_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="ascii") as handle:
                handle.write(base64.b64encode(raw).decode("ascii"))
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        self._key = raw
        return raw

    def encrypt(self, data: bytes) -> bytes:
        key = self._load_key()
        enc_key = hashlib.sha256(b"enc|" + key).digest()
        mac_key = hashlib.sha256(b"mac|" + key).digest()
        nonce = _secrets.token_bytes(16)
        body = bytes(a ^ b for a, b in zip(data, _keystream(enc_key, nonce, len(data))))
        tag = hmac.new(mac_key, nonce + body, hashlib.sha256).digest()
        return b"FK1" + nonce + tag + body

    def decrypt(self, blob: bytes) -> bytes:
        if not blob.startswith(b"FK1") or len(blob) < 3 + 16 + 32:
            raise VaultError("Запись хранилища DFY повреждена")
        key = self._load_key()
        enc_key = hashlib.sha256(b"enc|" + key).digest()
        mac_key = hashlib.sha256(b"mac|" + key).digest()
        nonce, tag, body = blob[3:19], blob[19:51], blob[51:]
        if not hmac.compare_digest(tag, hmac.new(mac_key, nonce + body, hashlib.sha256).digest()):
            raise VaultError("Запись хранилища DFY не прошла проверку целостности")
        return bytes(a ^ b for a, b in zip(body, _keystream(enc_key, nonce, len(body))))

def normalize_domain(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    try:
        host = urllib.parse.urlsplit(raw).hostname or ""
    except ValueError:
        return ""
    host = host.strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host[:253]

def domain_matches(bound: str, url: str) -> bool:
    """``bound`` = example.com accepts example.com and *.example.com over https (or localhost)."""
    bound = normalize_domain(bound)
    if not bound:
        return False
    try:
        parts = urllib.parse.urlsplit(str(url or ""))
    except ValueError:
        return False
    host = (parts.hostname or "").lower().strip(".")
    if not host:
        return False
    if parts.scheme != "https" and host not in ("localhost", "127.0.0.1"):
        return False
    return host == bound or host.endswith("." + bound)

def card_brand(number: str) -> str:
    digits = re.sub(r"\D", "", number or "")
    if re.match(r"^4", digits):
        return "Visa"
    if re.match(r"^(5[1-5]|2[2-7])", digits):
        return "Mastercard"
    if re.match(r"^3[47]", digits):
        return "Amex"
    if re.match(r"^220[0-4]", digits):
        return "МИР"
    if re.match(r"^(62|81)", digits):
        return "UnionPay"
    return "Карта"

def luhn_ok(number: str) -> bool:
    digits = re.sub(r"\D", "", number or "")
    if not 12 <= len(digits) <= 19:
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0

_CARD_IN_TEXT = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")

def looks_like_card(text: str) -> bool:
    return any(luhn_ok(match.group(0)) for match in _CARD_IN_TEXT.finditer(str(text or "")))

def scrub_cards(text: str) -> str:
    def repl(match: re.Match) -> str:
        return "[номер карты скрыт]" if luhn_ok(match.group(0)) else match.group(0)
    return _CARD_IN_TEXT.sub(repl, str(text or ""))

def normalize_phone(value: Any) -> str:
    """An international number as ``+<digits>`` (E.164). Raises VaultError without a country code.

    ``8XXXXXXXXXX`` (11 digits) is the Russian trunk form and becomes ``+7XXXXXXXXXX``; ``00`` means ``+``.
    Other numbers without a country code are refused instead of guessed."""
    raw = str(value or "").strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("00"):
        digits = digits[2:]
    elif not raw.startswith("+"):
        if len(digits) == 11 and digits[0] == "8":
            digits = "7" + digits[1:]
        elif not (len(digits) == 11 and digits[0] == "7"):
            raise VaultError("Укажите телефон с кодом страны, например +7 916 123-45-67 или +370 612 34567")
    if not 8 <= len(digits) <= 15 or digits[0] == "0":
        raise VaultError("Телефон должен содержать код страны и 8–15 цифр")
    return "+" + digits

def split_phone(phone: str) -> tuple[str, str]:
    """``+79161234567`` → ``("+7", "9161234567")`` (longest known calling code)."""
    digits = re.sub(r"\D", "", phone or "")
    for size in (3, 2, 1):
        if digits[:size] in _CALLING_CODES and len(digits) > size:
            return "+" + digits[:size], digits[size:]
    return "+" + digits[:1], digits[1:]

def normalize_date(value: Any) -> str:
    """``YYYY-MM-DD`` from ``YYYY-MM-DD`` / ``DD.MM.YYYY`` / ``DD/MM/YYYY``; VaultError otherwise."""
    raw = str(value or "").strip()
    match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", raw)
    if match:
        year, month, day = match.groups()
    else:
        match = re.fullmatch(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", raw)
        if not match:
            raise VaultError("Дата должна быть в виде ДД.ММ.ГГГГ")
        day, month, year = match.groups()
    try:
        import datetime
        return datetime.date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        raise VaultError("Такой даты нет") from None

def derived_fields(kind: str, values: Dict[str, str]) -> Dict[str, str]:
    """Extra read-only fields computed from stored ones, so the agent can fill forms that want
    another format (national phone, DD.MM.YYYY, "MM/YY", a split one-time code) without seeing values."""
    out: Dict[str, str] = {}
    phone = values.get("phone", "")
    if phone.startswith("+"):
        code, national = split_phone(phone)
        out.update(phone_country_code=code, phone_national=national, phone_digits=phone[1:])
    for name in ("birth_date", "issue_date", "expiry_date"):
        stamp = values.get(name, "")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stamp):
            year, month, day = stamp.split("-")
            out[name + "_dmy"] = f"{day}.{month}.{year}"
            if name == "birth_date":
                out.update(birth_day=day, birth_month=month, birth_year=year)
    names = [values.get(k, "") for k in ("first_name", "middle_name", "last_name")]
    if values.get("first_name") and values.get("last_name"):
        out["full_name"] = " ".join(x for x in names if x)
        out["fio"] = " ".join(x for x in (names[2], names[0], names[1]) if x)
    if kind == "card" and values.get("exp_month") and values.get("exp_year"):
        month, year = values["exp_month"].zfill(2)[-2:], values["exp_year"][-2:]
        out.update(exp=f"{month}/{year}", exp_year_short=year)
    if kind == "otp":
        code = values.get("code", "")
        out.update({f"code_{index + 1}": char for index, char in enumerate(code[:12])})
    return out

def _mask_phone(phone: str) -> str:
    code, national = split_phone(phone)
    return f"{code} •••• ••{national[-2:]}" if national else "•••"

def _mask(kind: str, fields: Dict[str, str], label: str, domain: str) -> str:
    if kind == "card":
        digits = re.sub(r"\D", "", fields.get("number", ""))
        return f"{card_brand(digits)} •••• {digits[-4:] if len(digits) >= 4 else '????'}"
    if kind == "login":
        user = fields.get("username", "")
        shown = (user[:2] + "•••" + (user[user.find("@"):] if "@" in user else "")) if user else "•••"
        return f"{shown} @ {domain or 'любой сайт'}"
    if kind == "phone":
        return _mask_phone(fields.get("phone", ""))
    if kind == "email":
        email = fields.get("email", "")
        return email[:2] + "•••" + email[email.find("@"):] if "@" in email else "•••"
    if kind == "address":
        kinds = {"shipping": "доставка", "billing": "плательщик", "both": "доставка и оплата"}
        place = ", ".join(x for x in (fields.get("city", ""), fields.get("country", "")) if x) or "адрес"
        prefix = kinds.get(fields.get("address_type", ""), "")
        return (f"{prefix}: " if prefix else "") + place + ", •••"
    if kind == "document":
        number = re.sub(r"\s", "", fields.get("number", ""))
        return f"{fields.get('doc_type') or label or 'Документ'} ••••{number[-2:]}"
    if kind == "company":
        inn = fields.get("inn", "")
        return (fields.get("company_name") or label or "Организация") + (f" (ИНН ••••{inn[-2:]})" if inn else "")
    if kind == "otp":
        return "одноразовый код ••••"
    if kind == "identity":
        name = " ".join(x for x in (fields.get("first_name", ""), fields.get("last_name", "")[:1]) if x)
        extra = " · ".join(x for x in (_mask_phone(fields["phone"]) if fields.get("phone", "").startswith("+") else "",
                                        "e-mail" if fields.get("email") else "") if x)
        return (name + "." if name else (label or "Данные")) + " (личные данные" + (f": {extra}" if extra else "") + ")"
    return label or "Секрет"

def _clean_fields(kind: str, fields: Any) -> Dict[str, str]:
    if not isinstance(fields, dict):
        raise VaultError("Нет полей секрета")
    allowed = KIND_FIELDS.get(kind, ())
    out: Dict[str, str] = {}
    for key, value in fields.items():
        name = str(key or "").strip().lower()
        if not _FIELD_RE.match(name):
            continue
        if kind != "identity" and name not in allowed:
            continue
        text = str(value if value is not None else "").strip() if name != "password" else str(value or "")
        if text:
            out[name] = text[:2000]
    if kind == "login" and not out.get("password"):
        raise VaultError("Для логина нужен пароль")
    if kind == "card":
        number = re.sub(r"\D", "", out.get("number", ""))
        if not luhn_ok(number):
            raise VaultError("Номер карты некорректен")
        out["number"] = number
        if not re.fullmatch(r"\d{3,4}", out.get("cvc", "") or "000"):
            raise VaultError("CVC должен состоять из 3–4 цифр")
    if "phone" in out:
        out["phone"] = normalize_phone(out["phone"])
    if "email" in out:
        out["email"] = out["email"].lower() if "@" in out["email"] else out["email"]
        if not _EMAIL_RE.match(out["email"]):
            raise VaultError("Некорректный e-mail")
    for name in ("birth_date", "issue_date", "expiry_date"):
        if name in out:
            out[name] = normalize_date(out[name])
    if "inn" in out and not re.fullmatch(r"\d{10}|\d{12}", out["inn"]):
        raise VaultError("ИНН — 10 или 12 цифр")
    if "address_type" in out and out["address_type"] not in ADDRESS_TYPES:
        out["address_type"] = "both"
    if kind == "otp":
        code = re.sub(r"[\s-]", "", out.get("code", ""))
        if not re.fullmatch(r"[A-Za-z0-9]{3,12}", code):
            raise VaultError("Код — от 3 до 12 цифр или латинских букв")
        out["code"] = code
    required = {"text": "value", "phone": "phone", "email": "email", "otp": "code"}.get(kind)
    if required and not out.get(required):
        raise VaultError("Пустой секрет" if kind == "text" else "Заполните поле")
    if not out:
        raise VaultError("Нет полей секрета")
    return out

def _phone_pattern(phone: str) -> Optional[re.Pattern]:
    """Matches the national part of a phone however a page formats it: ``(916) 123-45-67``."""
    national = split_phone(phone)[1]
    if len(national) < 6:
        return None
    return re.compile(r"[\s\-().]*".join(re.escape(char) for char in national))

class SecretVault:
    def __init__(self, root: Path, *, use_dpapi: Optional[bool] = None) -> None:
        self.root = Path(root)
        self.path = self.root / "dfy_vault.json"
        self._lock = threading.RLock()
        self.use_dpapi = (os.name == "nt") if use_dpapi is None else bool(use_dpapi)
        self._file_cipher = _FileKeyCipher(self.root / "dfy_vault.key")

        self._ephemeral: Dict[str, Dict[str, Any]] = {}

    @property
    def scheme(self) -> str:
        return "dpapi" if self.use_dpapi else "file-key"

    def info(self) -> Dict[str, Any]:
        return {"scheme": self.scheme,
                "note": ("Windows DPAPI: расшифровать может только ваша учётная запись Windows"
                         if self.use_dpapi else
                         "Резервное шифрование: ключ в отдельном файле с правами 600. Слабее DPAPI — "
                         "защищает файл хранилища, но не от программ, запущенных от вашего имени"),
                "note_en": ("Windows DPAPI: only your Windows account can decrypt it"
                            if self.use_dpapi else
                            "Fallback encryption: the key is in a separate file with 600 permissions. Weaker than DPAPI — "
                            "it protects the vault file, but not from programs running as you")}

    def _encrypt(self, data: bytes) -> str:
        blob = _dpapi(data, protect=True) if self.use_dpapi else self._file_cipher.encrypt(data)
        return base64.b64encode(blob).decode("ascii")

    def _decrypt(self, value: str) -> bytes:
        blob = base64.b64decode(value)
        return _dpapi(blob, protect=False) if self.use_dpapi else self._file_cipher.decrypt(blob)

    def _read(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return {"version": 1, "scheme": self.scheme, "items": {}}
        if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
            return {"version": 1, "scheme": self.scheme, "items": {}}
        return data

    def _write(self, data: Dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def add(self, kind: str, fields: Dict[str, Any], *, label: str = "", domain: str = "",
            persist: bool = True) -> Dict[str, Any]:
        kind = str(kind or "").lower()
        if kind not in KINDS:
            raise VaultError("Неизвестный тип секрета")
        clean = _clean_fields(kind, fields)
        dom = normalize_domain(domain)
        if kind == "login" and not dom:
            raise VaultError("Логин нужно привязать к сайту (домену)")
        label = str(label or "").strip()[:80]
        persist = bool(persist) and kind != "otp"
        secret_id = "s" + _secrets.token_hex(6)
        meta = {"id": secret_id, "kind": kind, "label": label, "domain": dom,
                "mask": _mask(kind, clean, label, dom)[:120], "fields": sorted(clean),
                "created": int(time.time()), "persistent": persist}
        with self._lock:
            self._store(meta, clean)
        return dict(meta)

    def _store(self, meta: Dict[str, Any], clean: Dict[str, str]) -> None:
        if meta["persistent"]:
            data = self._read()
            data["scheme"] = self.scheme
            data["items"][meta["id"]] = {**meta, "blob": self._encrypt(
                json.dumps(clean, ensure_ascii=False).encode("utf-8"))}
            self._write(data)
        else:
            self._ephemeral[meta["id"]] = {**meta, "values": clean}

    def update(self, secret_id: str, fields: Dict[str, Any], *, label: Optional[str] = None,
               domain: Optional[str] = None, clear: Optional[List[str]] = None) -> Dict[str, Any]:
        """Edit an entry in place (its id — and so every placeholder — stays valid).

        Values are write-only, so an empty field in ``fields`` means "keep"; ``clear`` lists the
        fields to remove."""
        with self._lock:
            meta = self.get_meta(secret_id)
            if not meta:
                raise VaultError("Секрет не найден")
            kind = meta["kind"]
            values = self._values(secret_id)
            for name in clear or []:
                values.pop(str(name), None)
            values.update({str(k): v for k, v in (fields or {}).items()
                           if v is not None and (str(v) if k == "password" else str(v).strip())})
            clean = _clean_fields(kind, values)
            dom = normalize_domain(domain) if domain is not None else meta.get("domain", "")
            if kind == "login" and not dom:
                raise VaultError("Логин нужно привязать к сайту (домену)")
            new_label = str(label).strip()[:80] if label is not None else meta.get("label", "")
            updated = {**{k: v for k, v in meta.items() if k not in ("blob", "values")},
                       "label": new_label, "domain": dom, "mask": _mask(kind, clean, new_label, dom)[:120],
                       "fields": sorted(clean), "updated": int(time.time())}
            self._store(updated, clean)
        return dict(updated)

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows = [{k: v for k, v in item.items() if k != "blob"}
                    for item in self._read()["items"].values() if isinstance(item, dict)]
            rows += [{k: v for k, v in item.items() if k != "values"} for item in self._ephemeral.values()]
        rows.sort(key=lambda row: -int(row.get("created") or 0))
        return rows

    def get_meta(self, secret_id: str) -> Optional[Dict[str, Any]]:
        return next((row for row in self.list() if row.get("id") == secret_id), None)

    def delete(self, secret_id: str) -> bool:
        with self._lock:
            if self._ephemeral.pop(secret_id, None) is not None:
                return True
            data = self._read()
            if data["items"].pop(str(secret_id), None) is None:
                return False
            self._write(data)
            return True

    def forget_ephemeral(self, ids: List[str]) -> None:
        with self._lock:
            for secret_id in ids:
                self._ephemeral.pop(secret_id, None)

    def _values(self, secret_id: str) -> Dict[str, str]:
        with self._lock:
            if secret_id in self._ephemeral:
                return dict(self._ephemeral[secret_id]["values"])
            item = self._read()["items"].get(secret_id)
            if not isinstance(item, dict):
                raise VaultError(f"Секрет {secret_id} не найден")
            try:
                return json.loads(self._decrypt(item["blob"]).decode("utf-8"))
            except VaultError:
                raise
            except Exception as exc:
                raise VaultError("Не удалось расшифровать секрет (другой пользователь Windows?)") from exc

    def public_for_model(self, ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        rows = self.list()
        if ids is not None:
            rows = [row for row in rows if row["id"] in ids]
        out = []
        for row in rows:
            fields = list(row.get("fields") or [])

            stub = {name: ("+10000000000" if name == "phone" else "2000-01-01" if name.endswith("_date")
                           else "x") for name in fields}
            derived = sorted(derived_fields(row["kind"], stub)) if row["kind"] != "otp" else \
                [f"code_{n}" for n in range(1, 7)]
            out.append({"id": row["id"], "kind": row["kind"], "label": row.get("label", ""),
                        "domain": row.get("domain", ""), "mask": row.get("mask", ""), "fields": fields,
                        **({"derived_fields": derived} if derived else {}),
                        "placeholder_example": "{{secret:%s.%s}}" % (row["id"], (fields or ["value"])[0])})
        return out

    def has_placeholder(self, text: str) -> bool:
        return bool(PLACEHOLDER_RE.search(str(text or "")))

    def resolve(self, text: str, page_url: str) -> str:
        """Substitute placeholders for real values. Raises VaultError on any policy violation."""
        def repl(match: re.Match) -> str:
            secret_id, field = match.group(1), match.group(2)
            meta = self.get_meta(secret_id)
            if not meta:
                raise VaultError(f"Секрет {secret_id} не найден")
            if meta["kind"] == "login" and not domain_matches(meta.get("domain", ""), page_url):
                raise VaultError(f"Логин привязан к {meta.get('domain')}; на этом сайте он не вводится")
            if meta["kind"] != "login" and meta.get("domain") and \
                    not domain_matches(meta["domain"], page_url):
                raise VaultError(f"Секрет привязан к {meta.get('domain')}; на этом сайте он не вводится")
            if meta["kind"] == "card":
                try:
                    scheme = urllib.parse.urlsplit(page_url).scheme
                except ValueError:
                    scheme = ""
                if scheme != "https":
                    raise VaultError("Данные карты вводятся только на HTTPS-страницах")
            values = self._values(secret_id)
            if field not in values:
                values = {**derived_fields(meta["kind"], values), **values}
            if field not in values:
                raise VaultError(f"У секрета нет поля {field}")
            return values[field]
        return PLACEHOLDER_RE.sub(repl, str(text or ""))

    def secret_strings(self, ids: Optional[List[str]] = None, *,
                       phone_patterns: Optional[List[re.Pattern]] = None) -> List[str]:
        """Every stored value (len >= 4) for redaction of logs / page text."""
        out: List[str] = []
        for row in self.list():
            if ids is not None and row["id"] not in ids:
                continue
            try:
                values = self._values(row["id"])
            except VaultError:
                continue
            for key, value in {**derived_fields(row["kind"], values), **values}.items():
                if key in NOT_REDACTED or key.startswith(("code_", "birth_day", "birth_month", "birth_year",
                                                          "phone_country", "exp_year_short")):
                    continue
                if key == "phone" and phone_patterns is not None:
                    pattern = _phone_pattern(value)
                    if pattern is not None:
                        phone_patterns.append(pattern)
                if len(value) >= 4:
                    out.append(value)
        return sorted(set(out), key=len, reverse=True)

    def redactor(self, ids: Optional[List[str]] = None) -> Callable[[Any], str]:
        patterns: List[re.Pattern] = []
        values = self.secret_strings(ids, phone_patterns=patterns)

        def redact(text: Any) -> str:
            result = str(text if text is not None else "")
            for value in values:
                if value in result:
                    result = result.replace(value, "[секрет]")
            for pattern in patterns:
                result = pattern.sub("[секрет]", result)
            return scrub_cards(result)
        return redact
