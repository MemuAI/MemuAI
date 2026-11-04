# -*- coding: utf-8 -*-
"""Encoders/decoders and hashes: Base64, URL, HTML, Unicode, hex, Base32/58/85, Punycode, JWT, ciphers, compression."""
from __future__ import annotations

import base64
import binascii
import bz2
import codecs
import gzip
import hashlib
import hmac as _hmac
import html
import json
import lzma
import quopri
import re
import urllib.parse
import zlib
from datetime import datetime, timezone
from email.header import decode_header, make_header

from .core import O, UtilError, decode_data, guess_mime, human_size, lines_of, utility

def _dir_opt(default: str = "encode"):
    return O("dir", "choice", "Направление", "Direction", default,
             choices=[("encode", "Закодировать", "Encode"), ("decode", "Раскодировать", "Decode")])

def _b64_text(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1251", "replace")

@utility("enc.base64", "code", "Base64", "Base64",
         "Текст ↔ Base64, включая URL-safe вариант. Раскодирует и data:URL.",
         "Text ↔ Base64, URL-safe variant included. Decodes data: URLs too.",
         opts=[_dir_opt(), O("urlsafe", "bool", "URL-safe (-_ вместо +/)", "URL-safe (-_ instead of +/)", False),
               O("nopad", "bool", "Без = в конце", "No = padding", False, show="dir=encode")],
         live=True, ai_default=True, keys="base64 b64 кодировать раскодировать")
def enc_base64(ctx):
    text = ctx.need_text()
    if ctx.o["dir"] == "encode":
        raw = text.encode("utf-8")
        out = (base64.urlsafe_b64encode(raw) if ctx.o["urlsafe"] else base64.b64encode(raw)).decode()
        return out.rstrip("=") if ctx.o["nopad"] else out
    data = re.sub(r"\s+", "", text)
    try:
        if data.startswith("data:"):
            raw = decode_data(data)
        else:
            data += "=" * (-len(data) % 4)
            raw = base64.urlsafe_b64decode(data) if ("-" in data or "_" in data or ctx.o["urlsafe"]) else base64.b64decode(data, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise UtilError(f"Это не Base64: {exc}", f"Not valid Base64: {exc}")
    if b"\x00" in raw[:2000] and len(raw) > 64:
        ext = _magic_ext(raw)
        ctx.out_bytes(f"decoded{ext}", raw)
        return {"text": "", "note": ctx.tr(f"Двоичные данные ({human_size(len(raw))}) сохранены файлом", f"Binary data ({human_size(len(raw), True)}) saved as a file")}
    return _b64_text(raw)

def _magic_ext(raw: bytes) -> str:
    for sig, ext in ((b"\x89PNG", ".png"), (b"\xff\xd8\xff", ".jpg"), (b"GIF8", ".gif"), (b"%PDF", ".pdf"), (b"PK\x03\x04", ".zip"),
                     (b"RIFF", ".wav"), (b"ID3", ".mp3"), (b"OggS", ".ogg"), (b"\x1f\x8b", ".gz"), (b"7z\xbc\xaf", ".7z"),
                     (b"Rar!", ".rar"), (b"\x00\x00\x01\x00", ".ico"), (b"BM", ".bmp"), (b"fLaC", ".flac")):
        if raw.startswith(sig):
            if sig == b"RIFF" and raw[8:12] == b"WEBP":
                return ".webp"
            return ext
    if raw[4:8] == b"ftyp":
        return ".mp4"
    return ".bin"

@utility("enc.file_base64", "code", "Файл → Base64 / data:URL", "File → Base64 / data URL",
         "Любой файл в Base64 или data:URL — для HTML, CSS, JSON и API.",
         "Any file as Base64 or a data URL — for HTML, CSS, JSON and APIs.",
         input="file", opts=[O("format", "choice", "Формат", "Format", "dataurl",
                                choices=[("dataurl", "data:URL", "data URL"), ("plain", "Чистый Base64", "Plain Base64"),
                                         ("css", "CSS background", "CSS background"), ("html", "HTML <img>", "HTML <img>")])],
         ai_default=True, keys="base64 data url файл картинка в base64 file to base64")
def enc_file_base64(ctx):
    f = ctx.file()
    b64 = base64.b64encode(f.data).decode()
    mime = guess_mime(f.name)
    url = f"data:{mime};base64,{b64}"
    fmt = ctx.o["format"]
    out = {"plain": b64, "dataurl": url, "css": f"background-image: url(\"{url}\");",
           "html": f'<img src="{url}" alt="{html.escape(f.stem)}">'}[fmt]
    if len(out) > 200_000:
        ctx.out_text(f"{f.stem}.base64.txt", out)
        return {"text": out[:2000] + "…", "note": ctx.tr("Результат большой — полностью он в файле .txt", "The result is big — the full text is in the .txt file")}
    return out

@utility("enc.base64_file", "code", "Base64 / data:URL → файл", "Base64 / data URL → file",
         "Восстанавливает файл из Base64 или data:URL; тип определяется по содержимому.",
         "Rebuilds a file from Base64 or a data URL; the type is detected from the content.",
         opts=[O("name", "text", "Имя файла (необязательно)", "File name (optional)", "")], ai_default=True,
         keys="base64 в файл декодировать картинку base64 to file")
def enc_base64_file(ctx):
    text = re.sub(r"\s+", "", ctx.need_text("Вставьте Base64 или data:URL", "Paste Base64 or a data URL"))
    try:
        raw = decode_data(text if text.startswith("data:") else text.replace("-", "+").replace("_", "/"))
    except (binascii.Error, ValueError) as exc:
        raise UtilError(f"Это не Base64: {exc}", f"Not valid Base64: {exc}")
    name = ctx.o["name"].strip() or ("file" + _magic_ext(raw))
    ctx.out_bytes(name, raw)
    return {"note": ctx.tr(f"Файл восстановлен: {human_size(len(raw))}", f"File restored: {human_size(len(raw), True)}")}

@utility("enc.url", "code", "URL-кодирование", "URL encode / decode",
         "Процентное кодирование для ссылок и параметров: пробел → %20, кириллица → %D0%BF….",
         "Percent-encoding for links and parameters: space → %20, non-ASCII → %D0%BF….",
         opts=[_dir_opt(), O("mode", "choice", "Что кодировать", "Scope", "component", show="dir=encode",
                             choices=[("component", "Значение параметра (всё)", "Parameter value (everything)"),
                                      ("url", "Целую ссылку (сохранить :/?&=)", "Whole URL (keep :/?&=)"),
                                      ("form", "Форма (пробел → +)", "Form (space → +)")])],
         live=True, ai_default=True, keys="url encode decode процент percent ссылка")
def enc_url(ctx):
    text = ctx.need_text()
    if ctx.o["dir"] == "decode":
        return urllib.parse.unquote_plus(text) if "+" in text and "%20" not in text else urllib.parse.unquote(text)
    mode = ctx.o["mode"]
    if mode == "url":
        return urllib.parse.quote(text, safe=":/?&=#%@!$'()*+,;~[]")
    if mode == "form":
        return urllib.parse.quote_plus(text)
    return urllib.parse.quote(text, safe="")

@utility("enc.html", "code", "HTML-сущности", "HTML entities",
         "< > & \" → &lt; &gt; &amp; &quot; и обратно; можно кодировать все не-ASCII символы.",
         "< > & \" → &lt; &gt; &amp; &quot; and back; optionally every non-ASCII character.",
         opts=[_dir_opt(), O("all", "bool", "Кодировать и не-ASCII (&#1087;)", "Encode non-ASCII too (&#1087;)", False, show="dir=encode")],
         live=True, ai_default=True, keys="html entities сущности escape экранирование")
def enc_html(ctx):
    text = ctx.need_text()
    if ctx.o["dir"] == "decode":
        return html.unescape(text)
    out = html.escape(text, quote=True)
    if ctx.o["all"]:
        out = "".join(ch if ord(ch) < 128 else f"&#{ord(ch)};" for ch in out)
    return out

@utility("enc.unicode", "code", "Unicode: \\u-коды и кодовые точки", "Unicode escapes and code points",
         "привет ↔ \\u043f\\u0440…, U+043F, &#x43F; и разбор каждого символа с его названием.",
         "Text ↔ \\u escapes, U+ code points, &#x; and a per-character breakdown with Unicode names.",
         opts=[O("mode", "choice", "Действие", "Action", "escape",
                 choices=[("escape", "Текст → \\uXXXX", "Text → \\uXXXX"), ("python", "Текст → \\N / \\U (Python)", "Text → Python escapes"),
                          ("points", "Текст → U+XXXX", "Text → U+XXXX"), ("css", "Текст → CSS \\XXXX", "Text → CSS \\XXXX"),
                          ("decode", "Раскодировать любые escape", "Decode any escapes"), ("inspect", "Разобрать по символам", "Inspect characters")])],
         live=True, ai_default=True, keys="unicode юникод escape символы code point кодовая точка")
def enc_unicode(ctx):
    import unicodedata
    text, mode = ctx.need_text(), ctx.o["mode"]
    if mode == "escape":
        return "".join(ch if 32 <= ord(ch) < 127 else (f"\\u{ord(ch):04x}" if ord(ch) <= 0xFFFF else
                       "".join(f"\\u{u:04x}" for u in _surrogates(ord(ch)))) for ch in text)
    if mode == "python":
        return text.encode("unicode_escape").decode("ascii")
    if mode == "points":
        return " ".join(f"U+{ord(ch):04X}" for ch in text)
    if mode == "css":
        return "".join(ch if 32 <= ord(ch) < 127 else f"\\{ord(ch):x} " for ch in text)
    if mode == "decode":
        out = re.sub(r"\\u\{([0-9a-fA-F]{1,6})\}", lambda m: chr(int(m.group(1), 16)), text)
        out = re.sub(r"\\U([0-9a-fA-F]{8})", lambda m: chr(int(m.group(1), 16)), out)
        out = re.sub(r"(?:\\u[dD][89abAB][0-9a-fA-F]{2}\\u[dD][c-fC-F][0-9a-fA-F]{2})",
                     lambda m: m.group(0).encode("ascii").decode("unicode_escape").encode("utf-16", "surrogatepass").decode("utf-16"), out)
        out = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), out)
        out = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), out)
        out = re.sub(r"U\+([0-9a-fA-F]{4,6})\s?", lambda m: chr(int(m.group(1), 16)), out)
        return html.unescape(out)
    rows = []
    for ch in text[:2000]:
        rows.append([ch if not ch.isspace() else repr(ch), f"U+{ord(ch):04X}", unicodedata.name(ch, "—"), unicodedata.category(ch),
                     ch.encode("utf-8").hex(" ").upper()])
    return {"table": {"columns": [ctx.tr("Символ", "Char"), ctx.tr("Код", "Code"), ctx.tr("Название", "Name"),
                                  ctx.tr("Категория", "Category"), "UTF-8"], "rows": rows}}

def _surrogates(code: int):
    code -= 0x10000
    return (0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF))

@utility("enc.hex", "code", "Hex, двоичный и байты", "Hex, binary and bytes",
         "Текст ↔ шестнадцатеричный, двоичный, восьмеричный или десятичный вид байтов (UTF-8/CP1251).",
         "Text ↔ hex, binary, octal or decimal bytes (UTF-8/CP1251).",
         opts=[_dir_opt(), O("format", "choice", "Вид", "Format", "hex",
                             choices=[("hex", "Hex (48 65 6C)", "Hex (48 65 6C)"), ("bin", "Двоичный (01001000)", "Binary (01001000)"),
                                      ("oct", "Восьмеричный", "Octal"), ("dec", "Десятичный (72 101)", "Decimal (72 101)")]),
               O("charset", "choice", "Кодировка", "Charset", "utf-8", choices=[("utf-8", "UTF-8", "UTF-8"), ("cp1251", "Windows-1251", "Windows-1251"), ("utf-16-le", "UTF-16 LE", "UTF-16 LE")]),
               O("sep", "bool", "Пробелы между байтами", "Spaces between bytes", True, show="dir=encode")],
         live=True, ai_default=True, keys="hex шестнадцатеричный двоичный binary bytes байты")
def enc_hex(ctx):
    text, fmt, cs = ctx.need_text(), ctx.o["format"], ctx.o["charset"]
    if ctx.o["dir"] == "encode":
        raw = text.encode(cs, "replace")
        parts = [{"hex": f"{b:02X}", "bin": f"{b:08b}", "oct": f"{b:03o}", "dec": str(b)}[fmt] for b in raw]
        return (" " if ctx.o["sep"] or fmt == "dec" else "").join(parts)
    clean = text.strip()
    try:
        if fmt == "hex":
            raw = bytes.fromhex(re.sub(r"0x|\\x|[\s,:-]", "", clean, flags=re.I))
        elif fmt == "bin":
            bits = re.sub(r"[^01]", "", clean)
            raw = bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits) - len(bits) % 8, 8))
        elif fmt == "oct":
            raw = bytes(int(x, 8) for x in clean.split())
        else:
            raw = bytes(int(x) for x in re.split(r"[\s,;]+", clean) if x)
    except ValueError as exc:
        raise UtilError(f"Не получилось разобрать: {exc}", f"Cannot parse: {exc}")
    return raw.decode(cs, "replace")

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

def _bx_encode(raw: bytes, alphabet: str) -> str:
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, r = divmod(n, len(alphabet))
        out = alphabet[r] + out
    pad = len(raw) - len(raw.lstrip(b"\x00"))
    return alphabet[0] * pad + (out or ("" if pad else alphabet[0]))

def _bx_decode(text: str, alphabet: str) -> bytes:
    n = 0
    for ch in text:
        if ch not in alphabet:
            raise UtilError(f"Символ «{ch}» не из алфавита", f"“{ch}” is not in the alphabet")
        n = n * len(alphabet) + alphabet.index(ch)
    pad = len(text) - len(text.lstrip(alphabet[0]))
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\x00" * pad + body

@utility("enc.basen", "code", "Base32, Base58, Base62, Base85", "Base32, Base58, Base62, Base85",
         "Другие варианты Base-кодирования: Base32 (TOTP-ключи), Base58 (биткоин), Base62, Ascii85/Base85.",
         "Other Base encodings: Base32 (TOTP keys), Base58 (Bitcoin), Base62, Ascii85/Base85.",
         opts=[_dir_opt(), O("alg", "choice", "Алфавит", "Alphabet", "b32",
                             choices=[("b32", "Base32", "Base32"), ("b32hex", "Base32hex", "Base32hex"), ("b58", "Base58", "Base58"),
                                      ("b62", "Base62", "Base62"), ("a85", "Ascii85", "Ascii85"), ("b85", "Base85 (RFC 1924)", "Base85 (RFC 1924)")])],
         live=True, keys="base32 base58 base62 base85 ascii85")
def enc_basen(ctx):
    text, alg = ctx.need_text(), ctx.o["alg"]
    if ctx.o["dir"] == "encode":
        raw = text.encode("utf-8")
        return {"b32": lambda: base64.b32encode(raw).decode(), "b32hex": lambda: base64.b32hexencode(raw).decode(),
                "b58": lambda: _bx_encode(raw, _B58), "b62": lambda: _bx_encode(raw, _B62),
                "a85": lambda: base64.a85encode(raw).decode(), "b85": lambda: base64.b85encode(raw).decode()}[alg]()
    clean = text.strip()
    try:
        if alg in ("b32", "b32hex"):
            clean = clean.replace(" ", "").upper()
            clean += "=" * (-len(clean) % 8)
            raw = base64.b32decode(clean) if alg == "b32" else base64.b32hexdecode(clean)
        elif alg == "b58":
            raw = _bx_decode(clean, _B58)
        elif alg == "b62":
            raw = _bx_decode(clean, _B62)
        elif alg == "a85":
            raw = base64.a85decode(clean, adobe=clean.startswith("<~"))
        else:
            raw = base64.b85decode(clean)
    except (binascii.Error, ValueError) as exc:
        raise UtilError(f"Не получилось раскодировать: {exc}", f"Cannot decode: {exc}")
    return _b64_text(raw)

@utility("enc.punycode", "code", "Punycode (кириллические домены)", "Punycode (IDN domains)",
         "пример.рф ↔ xn--e1afmkfd.xn--p1ai — для DNS, сертификатов и почты.",
         "пример.рф ↔ xn--e1afmkfd.xn--p1ai — for DNS, certificates and mail.",
         live=True, ai_default=True, keys="punycode idn домен рф xn--")
def enc_punycode(ctx):
    rows = []
    for line in lines_of(ctx.need_text()):
        value = line.strip()
        if not value:
            continue
        url = None
        if "://" in value:
            url = urllib.parse.urlsplit(value)
            host = url.hostname or ""
        else:
            host = value
        try:
            if "xn--" in host.lower():
                conv = ".".join(codecs.decode(p[4:].encode(), "punycode") if p.lower().startswith("xn--") else p for p in host.split("."))
            else:
                conv = host.encode("idna").decode()
        except (UnicodeError, ValueError) as exc:
            raise UtilError(f"«{host}»: {exc}", f"“{host}”: {exc}")
        rows.append(value.replace(host, conv) if url else conv)
    return "\n".join(rows)

def _b64url_json(part: str):
    raw = base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
    return json.loads(raw.decode("utf-8"))

@utility("enc.jwt", "code", "JWT: расшифровать и проверить", "JWT decoder and verifier",
         "Показывает заголовок и данные токена, сроки exp/iat/nbf человеческим языком; проверяет подпись HS256/384/512 секретом.",
         "Shows the header and payload, exp/iat/nbf as dates; verifies HS256/384/512 signatures with a secret.",
         opts=[O("secret", "password", "Секрет для проверки HS* (необязательно)", "Secret to verify HS* (optional)", "", ai=False)],
         live=True, ai_default=True, keys="jwt token токен bearer декодировать")
def enc_jwt(ctx):
    token = re.sub(r"^(Bearer\s+)", "", ctx.need_text("Вставьте JWT-токен", "Paste a JWT").strip(), flags=re.I)
    parts = token.split(".")
    if len(parts) != 3:
        raise UtilError("В JWT должно быть три части через точку", "A JWT has three dot-separated parts")
    try:
        header, payload = _b64url_json(parts[0]), _b64url_json(parts[1])
    except Exception as exc:  # noqa: BLE001
        raise UtilError(f"Не получилось прочитать токен: {exc}", f"Cannot read the token: {exc}")
    info = [[ctx.tr("Алгоритм", "Algorithm"), header.get("alg", "—")], [ctx.tr("Тип", "Type"), header.get("typ", "—")]]
    now = datetime.now(timezone.utc).timestamp()
    for key, ru, en in (("iat", "Выдан", "Issued"), ("nbf", "Действует с", "Not before"), ("exp", "Истекает", "Expires")):
        if isinstance(payload.get(key), (int, float)):
            when = datetime.fromtimestamp(payload[key], timezone.utc)
            extra = ""
            if key == "exp":
                left = payload[key] - now
                extra = ctx.tr(" — уже истёк" if left < 0 else f" — осталось {_ago(left, False)}",
                               " — expired" if left < 0 else f" — {_ago(left, True)} left")
            info.append([ctx.tr(ru, en), when.strftime("%Y-%m-%d %H:%M:%S UTC") + extra])
    for key in ("iss", "sub", "aud", "scope", "email", "name"):
        if key in payload:
            info.append([key, json.dumps(payload[key], ensure_ascii=False) if not isinstance(payload[key], str) else payload[key]])
    secret = ctx.o["secret"]
    alg = str(header.get("alg", ""))
    if secret:
        digest = {"HS256": hashlib.sha256, "HS384": hashlib.sha384, "HS512": hashlib.sha512}.get(alg)
        if not digest:
            info.append([ctx.tr("Подпись", "Signature"), ctx.tr(f"проверка {alg} секретом не поддерживается", f"{alg} cannot be checked with a secret")])
        else:
            sig = base64.urlsafe_b64encode(_hmac.new(secret.encode(), f"{parts[0]}.{parts[1]}".encode(), digest).digest()).rstrip(b"=").decode()
            ok = _hmac.compare_digest(sig, parts[2])
            info.append([ctx.tr("Подпись", "Signature"), ctx.tr("✓ верна" if ok else "✗ НЕ совпадает", "✓ valid" if ok else "✗ does NOT match")])
    text = "// header\n" + json.dumps(header, ensure_ascii=False, indent=2) + "\n\n// payload\n" + json.dumps(payload, ensure_ascii=False, indent=2)
    return {"info": info, "text": text, "lang": "json"}

def _ago(seconds: float, en: bool) -> str:
    seconds = abs(int(seconds))
    for size, ru, e in ((86400, "дн.", "d"), (3600, "ч", "h"), (60, "мин", "min")):
        if seconds >= size:
            return f"{seconds // size} {e if en else ru}"
    return f"{seconds} {'s' if en else 'с'}"

HASHES = ["md5", "sha1", "sha224", "sha256", "sha384", "sha512", "sha3_256", "sha3_512", "blake2b", "blake2s", "crc32", "adler32"]

def digest(name: str, data: bytes) -> str:
    if name == "crc32":
        return f"{zlib.crc32(data) & 0xFFFFFFFF:08x}"
    if name == "adler32":
        return f"{zlib.adler32(data) & 0xFFFFFFFF:08x}"
    return hashlib.new(name, data).hexdigest()

@utility("enc.hash", "code", "Хеши: MD5, SHA-256, CRC32…", "Hashes: MD5, SHA-256, CRC32…",
         "Контрольные суммы текста или файла (до 300 МБ) всеми популярными алгоритмами; сверка с ожидаемой суммой.",
         "Checksums of a text or a file (up to 300 MB) with every popular algorithm; compare with an expected value.",
         input="any", opts=[O("alg", "choice", "Алгоритм", "Algorithm", "all", choices=[("all", "Все сразу", "All")] + [(h, h.upper().replace("_", "-"), h.upper().replace("_", "-")) for h in HASHES]),
                            O("expect", "text", "Ожидаемая сумма для сверки", "Expected checksum", "")],
         live=True, ai_default=True, keys="хеш hash md5 sha256 sha1 crc32 контрольная сумма checksum")
def enc_hash(ctx):
    if ctx.files:
        data, what = ctx.files[0].data, ctx.files[0].name
    else:
        data, what = ctx.need_text("Введите текст или выберите файл", "Enter text or choose a file").encode("utf-8"), ""
    names = HASHES if ctx.o["alg"] == "all" else [ctx.o["alg"]]
    rows = [[n.upper().replace("_", "-"), digest(n, data)] for n in names]
    expect = ctx.o["expect"].strip().lower()
    note = what and ctx.tr(f"Файл: {what} ({human_size(len(data))})", f"File: {what} ({human_size(len(data), True)})")
    if expect:
        match = [r[0] for r in rows if r[1].lower() == expect]
        note = (note + " · " if note else "") + (ctx.tr(f"✓ совпадает с {match[0]}", f"✓ matches {match[0]}") if match else
                                                ctx.tr("✗ ни одна сумма не совпала", "✗ no checksum matches"))
    if len(rows) == 1:
        return {"text": rows[0][1], "note": note}
    return {"info": rows, "note": note}

@utility("enc.hmac", "code", "HMAC-подпись", "HMAC signature",
         "Подпись сообщения секретным ключом: HMAC-SHA256/SHA1/SHA512/MD5 в hex или Base64 (вебхуки, API).",
         "Sign a message with a secret: HMAC-SHA256/SHA1/SHA512/MD5 as hex or Base64 (webhooks, APIs).",
         opts=[O("key", "password", "Секретный ключ", "Secret key", "", ai=False),
               O("alg", "choice", "Алгоритм", "Algorithm", "sha256", choices=[("sha256", "SHA-256", "SHA-256"), ("sha1", "SHA-1", "SHA-1"), ("sha512", "SHA-512", "SHA-512"), ("md5", "MD5", "MD5")]),
               O("out", "choice", "Вывод", "Output", "hex", choices=[("hex", "hex", "hex"), ("b64", "Base64", "Base64")])],
         live=True, keys="hmac подпись signature webhook")
def enc_hmac(ctx):
    sig = _hmac.new(ctx.o["key"].encode(), ctx.need_text().encode(), ctx.o["alg"]).digest()
    return sig.hex() if ctx.o["out"] == "hex" else base64.b64encode(sig).decode()

_RU_ABC = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"

def _shift(ch: str, k: int) -> str:
    for abc in ("abcdefghijklmnopqrstuvwxyz", _RU_ABC):
        low = ch.lower()
        if low in abc:
            out = abc[(abc.index(low) + k) % len(abc)]
            return out.upper() if ch != low else out
    return ch

@utility("enc.cipher", "code", "Шифры: ROT13, Цезарь, Виженер, Атбаш", "Ciphers: ROT13, Caesar, Vigenère, Atbash",
         "Классические шифры для игр, квестов и загадок — латиница и кириллица.",
         "Classic ciphers for games, puzzles and quests — Latin and Cyrillic.",
         opts=[O("alg", "choice", "Шифр", "Cipher", "rot13", choices=[("rot13", "ROT13", "ROT13"), ("rot47", "ROT47", "ROT47"), ("caesar", "Цезарь", "Caesar"),
                                                                 ("vigenere", "Виженер", "Vigenère"), ("atbash", "Атбаш", "Atbash")]),
               O("shift", "int", "Сдвиг", "Shift", 3, min=-40, max=40, show="alg=caesar"),
               O("key", "text", "Ключевое слово", "Key word", "", show="alg=vigenere"),
               _dir_opt()],
         live=True, keys="шифр цезарь rot13 виженер atbash cipher caesar vigenere")
def enc_cipher(ctx):
    text, alg, decode = ctx.need_text(), ctx.o["alg"], ctx.o["dir"] == "decode"
    if alg == "rot13":
        return "".join(_shift(ch, 13) if ch.lower() in "abcdefghijklmnopqrstuvwxyz" else ch for ch in text)
    if alg == "rot47":
        return "".join(chr(33 + (ord(ch) - 33 + 47) % 94) if 33 <= ord(ch) <= 126 else ch for ch in text)
    if alg == "caesar":
        k = int(ctx.o["shift"]) * (-1 if decode else 1)
        return "".join(_shift(ch, k) for ch in text)
    if alg == "atbash":
        out = []
        for ch in text:
            low = ch.lower()
            for abc in ("abcdefghijklmnopqrstuvwxyz", _RU_ABC):
                if low in abc:
                    rep = abc[::-1][abc.index(low)]
                    out.append(rep.upper() if ch != low else rep)
                    break
            else:
                out.append(ch)
        return "".join(out)
    key = [ch.lower() for ch in ctx.o["key"] if ch.isalpha()]
    if not key:
        raise UtilError("Укажите ключевое слово", "Enter a key word")
    out, i = [], 0
    for ch in text:
        low = ch.lower()
        abc = "abcdefghijklmnopqrstuvwxyz" if low in "abcdefghijklmnopqrstuvwxyz" else _RU_ABC if low in _RU_ABC else ""
        if not abc:
            out.append(ch)
            continue
        kc = key[i % len(key)]
        k = abc.index(kc) if kc in abc else (ord(kc) % len(abc))
        out.append(_shift(ch, -k if decode else k))
        i += 1
    return "".join(out)

@utility("enc.mime", "code", "Почтовые кодировки (Quoted-Printable, =?UTF-8?…)", "Mail encodings (Quoted-Printable, =?UTF-8?…)",
         "Расшифровывает заголовки писем =?UTF-8?B?…?= и тела в Quoted-Printable (=D0=BF…); кодирует обратно.",
         "Decodes =?UTF-8?B?…?= mail headers and Quoted-Printable bodies (=D0=BF…); encodes back.",
         opts=[_dir_opt("decode"), O("mode", "choice", "Формат кодирования", "Encoding", "qp", show="dir=encode",
                                     choices=[("qp", "Quoted-Printable", "Quoted-Printable"), ("header", "Заголовок =?UTF-8?B?…?=", "Header =?UTF-8?B?…?=")])],
         live=True, keys="quoted printable mime заголовок письма email header")
def enc_mime(ctx):
    text = ctx.need_text()
    if ctx.o["dir"] == "decode":
        if "=?" in text:
            try:
                return str(make_header(decode_header(text)))
            except Exception:  # noqa: BLE001 - fall through to quoted-printable
                pass
        raw = quopri.decodestring(text.encode("latin-1", "replace"))
        return _b64_text(raw)
    if ctx.o["mode"] == "header":
        from email.header import Header
        return Header(text, "utf-8").encode()
    return quopri.encodestring(text.encode("utf-8")).decode("ascii")

@utility("enc.compress", "code", "Сжать текст (gzip/zlib → Base64)", "Compress text (gzip/zlib → Base64)",
         "Сжимает текст gzip, zlib, bzip2 или xz и выдаёт Base64 — и распаковывает такие строки обратно.",
         "Compresses text with gzip, zlib, bzip2 or xz into Base64 — and unpacks such strings back.",
         opts=[_dir_opt(), O("alg", "choice", "Алгоритм", "Algorithm", "gzip", choices=[("gzip", "gzip", "gzip"), ("zlib", "zlib/deflate", "zlib/deflate"), ("bz2", "bzip2", "bzip2"), ("xz", "xz/LZMA", "xz/LZMA")])],
         keys="gzip zlib сжать распаковать compress deflate base64")
def enc_compress(ctx):
    text, alg = ctx.need_text(), ctx.o["alg"]
    if ctx.o["dir"] == "encode":
        raw = text.encode("utf-8")
        packed = {"gzip": gzip.compress, "zlib": zlib.compress, "bz2": bz2.compress, "xz": lzma.compress}[alg](raw)
        out = base64.b64encode(packed).decode()
        return {"text": out, "note": ctx.tr(f"{len(raw)} байт → {len(packed)} байт ({len(packed) * 100 // max(1, len(raw))}%)",
                                            f"{len(raw)} bytes → {len(packed)} bytes ({len(packed) * 100 // max(1, len(raw))}%)")}
    try:
        packed = base64.b64decode(re.sub(r"\s+", "", text) + "=" * (-len(re.sub(r"\s+", "", text)) % 4))
    except (binascii.Error, ValueError) as exc:
        raise UtilError(f"Это не Base64: {exc}", f"Not valid Base64: {exc}")
    for fn in ({"gzip": gzip.decompress, "zlib": zlib.decompress, "bz2": bz2.decompress, "xz": lzma.decompress}[alg],
               gzip.decompress, zlib.decompress, lambda d: zlib.decompress(d, -15), bz2.decompress, lzma.decompress):
        try:
            return _b64_text(fn(packed))
        except Exception:  # noqa: BLE001 - try the next algorithm
            continue
    raise UtilError("Не удалось распаковать: данные не сжаты выбранным алгоритмом", "Cannot unpack: the data is not compressed with these algorithms")
