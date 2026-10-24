# -*- coding: utf-8 -*-
"""Панель «Использование» (Раунд 22): лимиты Claude, Codex и Antigravity в одном месте.

Подход — как у Quotty (github.com/confeden/Quotty), но честно и приватно:

* **Claude** — OAuth-токен Claude Code (``~/.claude/.credentials.json`` или ``$CLAUDE_CONFIG_DIR``;
  на macOS — связка ключей «Claude Code-credentials»), либо Claude Desktop на Windows
  (``config.json`` → ``oauth:tokenCacheV2``, AES-256-GCM, ключ в ``Local State`` под DPAPI), либо
  вставленный пользователем токен. Запрос — ``GET api.anthropic.com/api/oauth/usage``
  (окна ``five_hour``/``seven_day``/…: ``utilization`` в процентах и ``resets_at``).
* **Codex** — ``~/.codex/auth.json`` (``$CODEX_HOME``; общий у приложения и CLI) или вставленный
  токен; запрос — ``GET chatgpt.com/backend-api/codex/usage`` (``rate_limit.primary_window`` /
  ``secondary_window``: ``used_percent``, ``limit_window_seconds``, ``reset_at``/``reset_after_seconds``).
* **Antigravity** — облачного API нет: квоты живут в локальном языковом сервере, который запускает
  приложение/IDE. Порт и CSRF-токен берутся из ``~/.gemini/*/daemon/*.json``, журнала Electron или
  командной строки процесса ``language_server*``; запрос — ``RetrieveUserQuotaSummary`` на
  ``https://127.0.0.1:<порт>`` (самоподписанный сертификат — проверка отключена только для 127.0.0.1).

Правила: чужие файлы только читаются (токены не обновляются — это разлогинило бы CLI); вставленные
токены хранятся отдельно от panels.json, на Windows — под DPAPI текущего пользователя
(``CryptProtectData``); токены никогда не пишутся в журнал и не возвращаются API; на 429 —
пауза по ``Retry-After`` (30 с … 1 ч), последние значения остаются с пометкой «устарело»;
нет данных — честная стена, числа не выдумываются.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("memuai.panels.usage")

PROVIDERS = ("claude", "codex", "antigravity", "kiro", "proxy", "api_budget", "quota_planner")
MODES = {"claude": ("auto", "cli", "desktop", "dir", "token"), "codex": ("auto", "cli", "dir", "token"),
         "antigravity": ("auto", "dir"), "kiro": ("proxy",),
         "proxy": ("proxy",), "api_budget": ("proxy",), "quota_planner": ("proxy",)}
MAX_ACCOUNTS = 12
MIN_SPACING = 45.0            # одному аккаунту — не чаще раза в 45 с, даже по «Обновить»
COOLDOWN_DEFAULT = 300
COOLDOWN_MIN, COOLDOWN_MAX = 30, 3600
USER_AGENT = "MemuAI/3.1 (panels; usage)"
CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CODEX_USAGE_URLS = ("https://chatgpt.com/backend-api/codex/usage", "https://chatgpt.com/backend-api/wham/usage")
AG_QUOTA = "/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary"
AG_STATUS = "/exa.language_server_pb.LanguageServerService/GetUserStatus"
AG_BODY = '{"metadata":{"ideName":"antigravity"}}'
IS_WINDOWS = sys.platform == "win32"


class UsageWall(Exception):
    """Нет чисел: ``status`` — needs_login:… | source_gone:… | blocked:rate-limit …"""

    def __init__(self, status: str, message: str = "", *, retry_after: Optional[float] = None) -> None:
        super().__init__(message or status)
        self.status = status
        self.message = scrub(message)[:300]
        self.retry_after = retry_after


def scrub(text: Any) -> str:
    """Никаких токенов в сообщениях и журнале."""
    value = str(text or "")
    value = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1***", value)
    value = re.sub(r"sk-ant-[A-Za-z0-9_-]{6,}", "sk-ant-***", value)
    value = re.sub(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*", "***jwt***", value)
    value = re.sub(r"(?i)(csrf[_-]?token[=: \"]+)[A-Za-z0-9-]{6,}", r"\1***", value)
    return value


# ====================================================================== DPAPI

def _dpapi(data: bytes, *, protect: bool, entropy: bytes = b"MemuAI.panels.usage.v1") -> bytes:
    """CryptProtectData / CryptUnprotectData текущего пользователя (только Windows)."""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(DATA_BLOB), (wintypes.LPCWSTR if protect else ctypes.POINTER(wintypes.LPWSTR)),
                   ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    fn.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    src = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(src, ctypes.POINTER(ctypes.c_char)))
    ent = ctypes.create_string_buffer(entropy, len(entropy)) if entropy else None
    blob_ent = DATA_BLOB(len(entropy), ctypes.cast(ent, ctypes.POINTER(ctypes.c_char))) if entropy else None
    blob_out = DATA_BLOB()
    ok = fn(ctypes.byref(blob_in), None, ctypes.byref(blob_ent) if blob_ent else None, None, None,
            0x1, ctypes.byref(blob_out))       # CRYPTPROTECT_UI_FORBIDDEN
    if not ok:
        raise OSError(f"DPAPI error {ctypes.get_last_error()}")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def protect(text: str) -> Dict[str, str]:
    raw = text.encode("utf-8")
    if IS_WINDOWS:
        try:
            return {"scheme": "dpapi", "blob": base64.b64encode(_dpapi(raw, protect=True)).decode("ascii")}
        except Exception as exc:  # noqa: BLE001
            log.warning("usage: DPAPI protect failed: %s", type(exc).__name__)
    return {"scheme": "plain", "blob": base64.b64encode(raw).decode("ascii")}


def unprotect(record: Dict[str, Any]) -> str:
    blob = base64.b64decode(str(record.get("blob") or ""))
    if record.get("scheme") == "dpapi":
        return _dpapi(blob, protect=False).decode("utf-8")
    return blob.decode("utf-8")


def _secrets_path() -> Path:
    from .pgdata import MEMU_DIR, _profile_data_name
    try:
        name = _profile_data_name("panels-secrets.json")
    except Exception:
        name = "panels-secrets.json"
    return MEMU_DIR / name


def _read_secrets() -> Dict[str, Any]:
    path = _secrets_path()
    try:
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_secrets(data: Dict[str, Any]) -> None:
    path = _secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def put_secret(account_id: str, token: str, extra: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    token = str(token or "").strip()
    if not re.fullmatch(r"a_[a-z0-9]{6,24}", str(account_id or "")):
        raise ValueError("bad account id")
    if len(token) < 16 or len(token) > 8000 or re.search(r"\s", token):
        raise ValueError("Это не похоже на токен")
    data = _read_secrets()
    record = protect(json.dumps({"token": token, **(extra or {})}))
    record["at"] = time.time()
    data[account_id] = record
    _write_secrets(data)
    return {"scheme": record["scheme"]}


def get_secret(account_id: str) -> Optional[Dict[str, str]]:
    record = _read_secrets().get(str(account_id or ""))
    if not isinstance(record, dict):
        return None
    try:
        value = json.loads(unprotect(record))
        return value if isinstance(value, dict) else None
    except Exception:  # noqa: BLE001 — другой пользователь Windows / повреждено
        return None


def secret_info(account_id: str) -> Dict[str, Any]:
    record = _read_secrets().get(str(account_id or ""))
    if not isinstance(record, dict):
        return {"stored": False}
    return {"stored": True, "scheme": record.get("scheme"), "at": record.get("at")}


def drop_secret(account_id: str) -> None:
    data = _read_secrets()
    if data.pop(str(account_id or ""), None) is not None:
        _write_secrets(data)


# ====================================================================== AES-256-GCM (Claude Desktop)

def _aes_tables():
    sbox = [0] * 256
    p = q = 1
    while True:
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        x = q ^ ((q << 1) | (q >> 7)) ^ ((q << 2) | (q >> 6)) ^ ((q << 3) | (q >> 5)) ^ ((q << 4) | (q >> 4))
        sbox[p] = (x ^ 0x63) & 0xFF
        if p == 1:
            break
    sbox[0] = 0x63
    return sbox


_SBOX = _aes_tables()


def _xtime(a: int) -> int:
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else a << 1


def _expand_key(key: bytes) -> List[List[int]]:
    nk = len(key) // 4
    rounds = nk + 6
    words = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    rcon = 1
    for i in range(nk, 4 * (rounds + 1)):
        temp = list(words[i - 1])
        if i % nk == 0:
            temp = temp[1:] + temp[:1]
            temp = [_SBOX[b] for b in temp]
            temp[0] ^= rcon
            rcon = _xtime(rcon)
        elif nk > 6 and i % nk == 4:
            temp = [_SBOX[b] for b in temp]
        words.append([words[i - nk][j] ^ temp[j] for j in range(4)])
    return [sum(words[4 * r:4 * r + 4], []) for r in range(rounds + 1)]


def _aes_encrypt_block(round_keys: List[List[int]], block: bytes) -> bytes:
    s = [b ^ k for b, k in zip(block, round_keys[0])]
    last = len(round_keys) - 1
    for rnd in range(1, last + 1):
        s = [_SBOX[b] for b in s]
        s = [s[(i + 4 * (i % 4)) % 16] for i in range(16)]           # ShiftRows (столбцы по 4 байта)
        if rnd != last:
            out = []
            for c in range(4):
                a = s[4 * c:4 * c + 4]
                t = a[0] ^ a[1] ^ a[2] ^ a[3]
                out += [a[0] ^ t ^ _xtime(a[0] ^ a[1]), a[1] ^ t ^ _xtime(a[1] ^ a[2]),
                        a[2] ^ t ^ _xtime(a[2] ^ a[3]), a[3] ^ t ^ _xtime(a[3] ^ a[0])]
            s = out
        s = [b ^ k for b, k in zip(s, round_keys[rnd])]
    return bytes(s)


def _gmul(x: int, y: int) -> int:
    z, v = 0, y
    for i in range(127, -1, -1):
        if (x >> i) & 1:
            z ^= v
        v = (v >> 1) ^ (0xE1 << 120) if v & 1 else v >> 1
    return z


def aes_gcm_decrypt(key: bytes, nonce: bytes, data: bytes) -> bytes:
    """AES-GCM (96-битный nonce, тег 16 байт в конце). Библиотека cryptography — если есть."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
        return AESGCM(key).decrypt(nonce, data, None)
    except ImportError:
        pass
    if len(nonce) != 12 or len(data) < 16 or len(key) not in (16, 24, 32):
        raise ValueError("bad GCM input")
    ct, tag = data[:-16], data[-16:]
    rk = _expand_key(key)
    h = int.from_bytes(_aes_encrypt_block(rk, bytes(16)), "big")
    j0 = nonce + b"\x00\x00\x00\x01"
    ghash = 0
    for i in range(0, len(ct), 16):
        block = ct[i:i + 16].ljust(16, b"\x00")
        ghash = _gmul(ghash ^ int.from_bytes(block, "big"), h)
    ghash = _gmul(ghash ^ (len(ct) * 8), h)
    expected = (int.from_bytes(_aes_encrypt_block(rk, j0), "big") ^ ghash).to_bytes(16, "big")
    import hmac
    if not hmac.compare_digest(expected, tag):
        raise ValueError("GCM tag mismatch")
    out = bytearray()
    counter = int.from_bytes(j0[12:], "big")
    for i in range(0, len(ct), 16):
        counter = (counter + 1) & 0xFFFFFFFF
        stream = _aes_encrypt_block(rk, nonce + counter.to_bytes(4, "big"))
        chunk = ct[i:i + 16]
        out += bytes(a ^ b for a, b in zip(chunk, stream))
    return bytes(out)


# ====================================================================== HTTP (подменяется в тестах)

async def _default_http(method: str, url: str, headers: Dict[str, str], body: Optional[str] = None, *,
                        verify: bool = True, browser: bool = False, timeout: float = 20.0) -> Tuple[int, Dict[str, str], str]:
    if browser:
        try:
            from curl_cffi.requests import AsyncSession  # type: ignore
            async with AsyncSession(impersonate="chrome", timeout=timeout, verify=verify) as session:
                resp = await session.request(method, url, headers=headers, data=body)
                return resp.status_code, {k.lower(): v for k, v in resp.headers.items()}, resp.text
        except ImportError:
            pass
    import httpx
    from .tts_providers import trusts_env
    # Only the local Antigravity server skips the proxy: api.anthropic.com is reachable for many users only
    # through their system/VPN proxy (trust_env=False for every URL left the Claude limits «network error»).
    async with httpx.AsyncClient(timeout=timeout, verify=verify, follow_redirects=False,
                                 trust_env=trusts_env(url)) as client:
        resp = await client.request(method, url, headers=headers, content=body)
        return resp.status_code, {k.lower(): v for k, v in resp.headers.items()}, resp.text


HTTP: Callable[..., Awaitable[Tuple[int, Dict[str, str], str]]] = _default_http


def retry_after(headers: Dict[str, str]) -> float:
    value = str((headers or {}).get("retry-after") or "").strip()
    seconds: Optional[float] = None
    if re.fullmatch(r"\d+(\.\d+)?", value):
        seconds = float(value)
    elif value:
        try:
            seconds = parsedate_to_datetime(value).timestamp() - time.time()
        except (TypeError, ValueError, IndexError):
            seconds = None
    if not seconds or seconds <= 0:      # «Retry-After: 0» — это отсутствие заголовка, а не «сразу»
        seconds = COOLDOWN_DEFAULT
    return float(max(COOLDOWN_MIN, min(COOLDOWN_MAX, seconds)))


# ====================================================================== пути

def home() -> Path:
    return Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or Path.home())


def claude_dirs(account: Dict[str, Any]) -> List[Path]:
    if account.get("mode") == "dir" and account.get("dir"):
        return [Path(os.path.expandvars(os.path.expanduser(str(account["dir"]))))]
    out = []
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        out.append(Path(env))
    out.append(home() / ".claude")
    return out


def codex_dir(account: Dict[str, Any]) -> Path:
    if account.get("mode") == "dir" and account.get("dir"):
        return Path(os.path.expandvars(os.path.expanduser(str(account["dir"]))))
    env = os.environ.get("CODEX_HOME")
    return Path(env) if env else home() / ".codex"


def claude_desktop_dirs() -> List[Path]:
    roots: List[Path] = []
    for value in (os.environ.get("APPDATA"), str(home() / "AppData" / "Roaming")):
        if value and Path(value) not in roots:
            roots.append(Path(value))
    if sys.platform == "darwin":
        roots.append(home() / "Library" / "Application Support")
    dirs = [root / "Claude" for root in roots]
    local = os.environ.get("LOCALAPPDATA") or str(home() / "AppData" / "Local")
    try:
        for entry in (Path(local) / "Packages").iterdir():
            name = entry.name.lower()
            if name.startswith("claude") or "anthropic" in name:
                dirs += [entry / "LocalCache" / "Roaming" / "Claude", entry / "LocalCache" / "Local" / "Claude"]
    except OSError:
        pass
    return dirs


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    for attempt in range(3):
        try:
            data = json.loads(path.read_text("utf-8"))
            return data if isinstance(data, dict) else None
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            time.sleep(0.12)              # файл в этот момент атомарно перезаписывается
    return None


def jwt_claims(token: str) -> Dict[str, Any]:
    """Полезная нагрузка JWT — только для показа (план, срок), подпись не проверяется."""
    try:
        part = str(token).split(".")[1]
        part += "=" * (-len(part) % 4)
        data = json.loads(base64.urlsafe_b64decode(part.encode("ascii")))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


# ====================================================================== токены

def claude_cli_token(account: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for directory in claude_dirs(account):
        data = _read_json(directory / ".credentials.json")
        oauth = (data or {}).get("claudeAiOauth")
        if isinstance(oauth, dict) and oauth.get("accessToken"):
            return {"token": str(oauth["accessToken"]), "expires": oauth.get("expiresAt"),
                    "plan": oauth.get("subscriptionType"), "tier": oauth.get("rateLimitTier"),
                    "scopes": oauth.get("scopes") or [], "where": "Claude Code"}
    if sys.platform == "darwin" and account.get("mode") != "dir":
        try:
            raw = subprocess.run(["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                                 capture_output=True, text=True, timeout=5).stdout.strip()
            oauth = json.loads(raw).get("claudeAiOauth") if raw else None
            if isinstance(oauth, dict) and oauth.get("accessToken"):
                return {"token": str(oauth["accessToken"]), "expires": oauth.get("expiresAt"),
                        "plan": oauth.get("subscriptionType"), "tier": oauth.get("rateLimitTier"),
                        "scopes": oauth.get("scopes") or [], "where": "Claude Code"}
        except Exception:  # noqa: BLE001
            pass
    return None


def _chromium_key(local_state: Dict[str, Any]) -> bytes:
    encoded = str(((local_state or {}).get("os_crypt") or {}).get("encrypted_key") or "")
    raw = base64.b64decode(encoded)
    if raw[:5] != b"DPAPI":
        raise ValueError("unexpected key prefix")
    if not IS_WINDOWS:
        raise ValueError("Claude Desktop token decryption needs Windows DPAPI")
    return _dpapi(raw[5:], protect=False, entropy=b"")


def decrypt_token_cache(key: bytes, encoded: str) -> Dict[str, Any]:
    raw = base64.b64decode(encoded)
    if raw[:3] != b"v10" or len(raw) < 3 + 12 + 16:
        raise ValueError("unexpected token cache format")
    data = json.loads(aes_gcm_decrypt(key, raw[3:15], raw[15:]))
    return data if isinstance(data, dict) else {}


def claude_desktop_tokens(*, key_fn: Callable[[Dict[str, Any]], bytes] = _chromium_key,
                          dirs: Optional[List[Path]] = None) -> List[Dict[str, Any]]:
    """Все пригодные токены Claude Desktop (scope user:profile), свежий кэш V2 — первым."""
    best: Optional[Tuple[float, Path]] = None
    for directory in dirs if dirs is not None else claude_desktop_dirs():
        try:
            stamp = (directory / "config.json").stat().st_mtime
        except OSError:
            continue
        if (directory / "Local State").exists() and (best is None or stamp > best[0]):
            best = (stamp, directory)
    if best is None:
        return []
    directory = best[1]
    config = _read_json(directory / "config.json") or {}
    key = key_fn(_read_json(directory / "Local State") or {})
    now_ms = time.time() * 1000
    scored = []
    for cache_key, fresh in (("oauth:tokenCacheV2", True), ("oauth:tokenCache", False)):
        encoded = config.get(cache_key)
        if not isinstance(encoded, str):
            continue
        try:
            entries = decrypt_token_cache(key, encoded)
        except Exception:  # noqa: BLE001
            continue
        for scope, entry in entries.items():
            if not isinstance(entry, dict) or "user:profile" not in str(scope) or not entry.get("token"):
                continue
            expires = entry.get("expiresAt")
            if isinstance(expires, (int, float)) and expires <= now_ms:
                continue
            score = (4 if fresh else 0) + (2 if entry.get("subscriptionType") else 0) + 1
            scored.append((score, {"token": str(entry["token"]), "expires": expires, "plan": entry.get("subscriptionType"),
                                   "tier": entry.get("rateLimitTier"), "where": "Claude Desktop"}))
    scored.sort(key=lambda row: -row[0])
    return [row[1] for row in scored]


def claude_tokens(account: Dict[str, Any], secret: Optional[Dict[str, str]]) -> List[Dict[str, Any]]:
    mode = account.get("mode") or "auto"
    if mode == "token":
        return [{"token": secret["token"], "where": "token"}] if secret and secret.get("token") else []
    out: List[Dict[str, Any]] = []
    if mode in ("auto", "cli", "dir"):
        found = claude_cli_token(account)
        if found:
            out.append(found)
    if mode in ("auto", "desktop") and IS_WINDOWS:
        try:
            out.extend(claude_desktop_tokens())
        except Exception as exc:  # noqa: BLE001
            log.info("usage: Claude Desktop tokens unavailable: %s", scrub(exc))
    return out


def codex_auth(account: Dict[str, Any], secret: Optional[Dict[str, str]]) -> Optional[Dict[str, Any]]:
    if (account.get("mode") or "auto") == "token":
        if not secret or not secret.get("token"):
            return None
        claims = jwt_claims(secret["token"]).get("https://api.openai.com/auth") or {}
        return {"token": secret["token"], "account_id": secret.get("account_id") or claims.get("chatgpt_account_id"),
                "plan": claims.get("chatgpt_plan_type"), "exp": jwt_claims(secret["token"]).get("exp"), "where": "token"}
    data = _read_json(codex_dir(account) / "auth.json")
    if not data:
        return None
    tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
    access = str(tokens.get("access_token") or "")
    if not access:
        if data.get("OPENAI_API_KEY"):
            return {"api_key_only": True}
        return None
    claims = jwt_claims(access)
    auth = claims.get("https://api.openai.com/auth") or jwt_claims(str(tokens.get("id_token") or "")).get(
        "https://api.openai.com/auth") or {}
    return {"token": access, "account_id": tokens.get("account_id") or auth.get("chatgpt_account_id"),
            "plan": auth.get("chatgpt_plan_type"), "exp": claims.get("exp"), "where": "Codex"}


# ====================================================================== окна лимитов

def _ts(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        return number / 1000 if number > 1e11 else number
    text = str(value).strip()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return _ts(float(text))
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def make_limit(key: str, title: str, used: float, resets_at: Optional[float], window: Optional[float],
               now: float) -> Dict[str, Any]:
    used = max(0.0, min(100.0, float(used)))
    start = None
    if resets_at and window:
        candidate = resets_at - window
        start = candidate if candidate <= now else None     # часы отстают — начало не выдумываем
    return {"key": key, "title": title, "used": round(used, 1), "resets_at": resets_at, "window": window, "start": start}


def window_label(seconds: Optional[float], lang: str) -> str:
    if not seconds:
        return "окно" if lang != "en" else "window"
    hours = seconds / 3600
    if hours >= 24 * 6.5:
        days = round(hours / 24)
        if days == 7:
            return "Неделя" if lang != "en" else "Weekly"
        return f"{days} дн." if lang != "en" else f"{days}d"
    if hours >= 23.5:
        return "Сутки" if lang != "en" else "Daily"
    return f"{round(hours)} ч" if lang != "en" else f"{round(hours)}h"


CLAUDE_WINDOWS = {
    "five_hour": (5 * 3600, ("5 часов", "5-hour")),
    "seven_day": (7 * 86400, ("Неделя · все модели", "Weekly · all models")),
    "seven_day_opus": (7 * 86400, ("Неделя · Opus", "Weekly · Opus")),
    "seven_day_sonnet": (7 * 86400, ("Неделя · Sonnet", "Weekly · Sonnet")),
    "seven_day_oauth_apps": (7 * 86400, ("Неделя · приложения", "Weekly · apps")),
}


def claude_limits(payload: Dict[str, Any], lang: str, now: float) -> List[Dict[str, Any]]:
    rows = []
    for key, value in (payload or {}).items():
        if not isinstance(value, dict) or "utilization" not in value:
            continue
        util = value.get("utilization")
        if util is None:
            util = 0.0
        try:
            util = float(util)
        except (TypeError, ValueError):
            continue
        if key in CLAUDE_WINDOWS:
            window, titles = CLAUDE_WINDOWS[key]
            title = titles[1] if lang == "en" else titles[0]
        else:
            if key in ("extra_usage",) and not value.get("is_enabled"):
                continue
            window = 7 * 86400 if key.startswith("seven_day") else 5 * 3600 if key.startswith("five_hour") else None
            title = key.replace("_", " ").capitalize()
        rows.append(make_limit(key, title, util, _ts(value.get("resets_at")), window, now))
    order = list(CLAUDE_WINDOWS)
    rows.sort(key=lambda r: order.index(r["key"]) if r["key"] in order else 99)
    return rows


def codex_limits(payload: Dict[str, Any], lang: str, now: float) -> List[Dict[str, Any]]:
    rows = []
    rate = (payload or {}).get("rate_limit") if isinstance((payload or {}).get("rate_limit"), dict) else {}
    for key in ("primary_window", "secondary_window"):
        win = rate.get(key)
        if not isinstance(win, dict) or win.get("used_percent") is None:
            continue
        try:
            used = float(win["used_percent"])
        except (TypeError, ValueError):
            continue
        resets = _ts(win.get("reset_at"))
        if resets is None and win.get("reset_after_seconds") is not None:
            try:
                resets = now + float(win["reset_after_seconds"])
            except (TypeError, ValueError):
                resets = None
        window = None
        try:
            window = float(win["limit_window_seconds"]) if win.get("limit_window_seconds") else None
        except (TypeError, ValueError):
            window = None
        rows.append(make_limit(key, window_label(window, lang), used, resets, window, now))
    rows.sort(key=lambda r: r["window"] or 0)
    return rows


def _ag_window_seconds(word: str) -> float:
    word = str(word or "").strip().lower()
    named = {"daily": 86400, "weekly": 7 * 86400, "monthly": 30 * 86400}
    if word in named:
        return float(named[word])
    match = re.fullmatch(r"(\d+)\s*([hd])", word)
    if match:
        return float(int(match.group(1)) * (3600 if match.group(2) == "h" else 86400))
    return 5 * 3600.0


def _fraction(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0                           # protobuf JSON опускает ноль: нет поля = исчерпано


def antigravity_limits(summary: Optional[Dict[str, Any]], status: Optional[Dict[str, Any]], lang: str,
                       now: float) -> List[Dict[str, Any]]:
    rows: List[Tuple[int, float, Dict[str, Any]]] = []
    groups = (((summary or {}).get("response") or {}).get("groups")) or []
    for group in groups if isinstance(groups, list) else []:
        name = str((group or {}).get("displayName") or "")
        pool = 0 if "gemini" in name.lower() else 1
        title = "Gemini" if pool == 0 else "Claude / GPT"
        for bucket in (group or {}).get("buckets") or []:
            if not isinstance(bucket, dict):
                continue
            window = _ag_window_seconds(bucket.get("window"))
            used = (1 - _fraction(bucket.get("remainingFraction"))) * 100
            rows.append((pool, window, make_limit(f"{pool}:{bucket.get('window')}", f"{title} · {window_label(window, lang)}",
                                                  used, _ts(bucket.get("resetTime")), window, now)))
    if not rows and status:
        configs = ((((status or {}).get("userStatus") or {}).get("cascadeModelConfigData") or {}).get("clientModelConfigs")) or []
        worst: Dict[int, Tuple[float, Optional[float]]] = {}
        for cfg in configs if isinstance(configs, list) else []:
            quota = (cfg or {}).get("quotaInfo")
            if not isinstance(quota, dict):
                continue
            pool = 0 if "gemini" in str(cfg.get("label") or "").lower() else 1
            remaining = _fraction(quota.get("remainingFraction"))
            reset = _ts(quota.get("resetTime"))
            old = worst.get(pool)
            if old is None:
                worst[pool] = (remaining, reset)
            else:
                worst[pool] = (min(old[0], remaining), min([t for t in (old[1], reset) if t] or [None]))
        for pool, (remaining, reset) in worst.items():
            title = "Gemini" if pool == 0 else "Claude / GPT"
            rows.append((pool, 5 * 3600, make_limit(f"{pool}:5h", f"{title} · {window_label(5 * 3600, lang)}",
                                                    (1 - remaining) * 100, reset, 5 * 3600, now)))
    rows.sort(key=lambda r: (r[0], r[1]))
    return [row[2] for row in rows]


def claude_plan(plan: Any, tier: Any) -> str:
    plan, tier = str(plan or "").lower(), str(tier or "").lower()
    if plan == "max":
        return "Max 20×" if "20x" in tier else "Max 5×" if "5x" in tier else "Max"
    if plan:
        return plan.capitalize()
    return ""


# ====================================================================== провайдеры

def _expired(stamp: Any, now: float) -> bool:
    if not isinstance(stamp, (int, float)):
        return False
    seconds = stamp / 1000 if stamp > 1e11 else stamp
    return seconds <= now


async def fetch_claude(account: Dict[str, Any], secret: Optional[Dict[str, str]], lang: str, now: float) -> Dict[str, Any]:
    tokens = claude_tokens(account, secret)
    if not tokens:
        mode = account.get("mode") or "auto"
        hint = ("Вставьте токен ещё раз" if mode == "token" else
                "Войдите в Claude Code (claude → /login) или откройте Claude Desktop")
        raise UsageWall("needs_login:claude", f"Токен Claude не найден. {hint}.")
    if all(_expired(t.get("expires"), now) for t in tokens):
        raise UsageWall("needs_login:claude", "Токен Claude истёк — откройте Claude Code или Claude Desktop, они обновят его.")
    throttled: Optional[float] = None
    last = ""
    for tok in tokens:
        if _expired(tok.get("expires"), now):
            continue
        status, headers, text = await HTTP("GET", CLAUDE_USAGE_URL, {
            "Authorization": f"Bearer {tok['token']}", "anthropic-beta": "oauth-2025-04-20",
            "anthropic-version": "2023-06-01", "User-Agent": USER_AGENT, "Accept": "application/json"})
        if status == 200:
            try:
                payload = json.loads(text)
            except ValueError as exc:
                raise UsageWall("blocked:bad-response", "Anthropic вернул не JSON") from exc
            return {"plan": claude_plan(tok.get("plan"), tok.get("tier")), "via": tok.get("where") or "",
                    "limits": claude_limits(payload, lang, now)}
        if status == 429:
            throttled = max(throttled or 0, retry_after(headers))
            continue
        if status in (401, 403):
            last = f"HTTP {status}"
            continue                          # этот токен не тот (без scope user:profile или устарел)
        last = f"HTTP {status}"
        break
    if throttled:
        raise UsageWall("blocked:rate-limit", "Anthropic ограничил запросы", retry_after=throttled)
    if last in ("HTTP 401", "HTTP 403"):
        raise UsageWall("needs_login:claude", "Anthropic не принял токен — войдите в Claude Code заново.")
    raise UsageWall("blocked:source-down", f"api.anthropic.com: {last or 'нет ответа'}")


async def fetch_codex(account: Dict[str, Any], secret: Optional[Dict[str, str]], lang: str, now: float) -> Dict[str, Any]:
    auth = codex_auth(account, secret)
    if not auth:
        raise UsageWall("needs_login:codex", "Codex не найден — войдите в Codex CLI (codex login) или вставьте токен.")
    if auth.get("api_key_only"):
        raise UsageWall("needs_login:codex", "Codex вошёл по API-ключу: у ключа нет окон лимитов подписки ChatGPT.")
    if _expired(auth.get("exp"), now):
        raise UsageWall("needs_login:codex", "Токен Codex устарел — откройте Codex, он обновит его.")
    headers = {"Authorization": f"Bearer {auth['token']}", "User-Agent": USER_AGENT, "Accept": "application/json"}
    if auth.get("account_id"):
        headers["chatgpt-account-id"] = str(auth["account_id"])
    status, resp_headers, text = 0, {}, ""
    for url in CODEX_USAGE_URLS:
        status, resp_headers, text = await HTTP("GET", url, headers, browser=True)
        if status != 404:
            break
    if status == 200:
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise UsageWall("blocked:bad-response", "chatgpt.com вернул не JSON (возможно, защита от ботов)") from exc
        plan = str(payload.get("plan_type") or auth.get("plan") or "")
        return {"plan": plan.capitalize() if plan else "", "via": auth.get("where") or "",
                "limits": codex_limits(payload, lang, now)}
    if status == 429:
        raise UsageWall("blocked:rate-limit", "OpenAI ограничил запросы", retry_after=retry_after(resp_headers))
    if status == 401:
        raise UsageWall("needs_login:codex", "Токен Codex устарел — откройте Codex, он обновит его.")
    if status == 403:
        raise UsageWall("blocked:forbidden", "chatgpt.com отказал (403) — возможно, защита от ботов; попробую позже.")
    raise UsageWall("blocked:source-down", f"chatgpt.com: HTTP {status or 'нет ответа'}")


# ------------------------------------------------------------- Antigravity: поиск языкового сервера

def _gemini_root(account: Dict[str, Any]) -> Path:
    if account.get("mode") == "dir" and account.get("dir"):
        return Path(os.path.expandvars(os.path.expanduser(str(account["dir"]))))
    return home() / ".gemini"


def ag_from_daemon_files(root: Path) -> List[Dict[str, Any]]:
    out = []
    dirs = [root / "daemon"] if (root / "daemon").is_dir() else []
    try:
        dirs += [sub / "daemon" for sub in root.iterdir() if (sub / "daemon").is_dir()]
    except OSError:
        pass
    for directory in dirs:
        try:
            files = list(directory.glob("*.json"))
        except OSError:
            continue
        for path in files:
            data = _read_json(path) or {}
            port, csrf = data.get("httpsPort"), data.get("csrfToken")
            if isinstance(port, int) and 0 < port < 65536 and csrf:
                try:
                    seen = path.stat().st_mtime
                except OSError:
                    seen = 0
                out.append({"port": port, "csrf": str(csrf), "seen": seen, "from": "daemon"})
    return out


def ag_from_logs() -> List[Dict[str, Any]]:
    out = []
    bases = [os.environ.get("APPDATA"), str(home() / "AppData" / "Roaming")]
    if sys.platform == "darwin":
        bases.append(str(home() / "Library" / "Application Support"))
    for base in dict.fromkeys(b for b in bases if b):
        for app in ("Antigravity", "Antigravity IDE"):
            path = Path(base) / app / "logs" / "main.log"
            try:
                size = path.stat().st_size
                with path.open("rb") as handle:
                    handle.seek(max(0, size - 256 * 1024))
                    tail = handle.read().decode("utf-8", "replace")
                seen = path.stat().st_mtime
            except OSError:
                continue
            csrf = re.findall(r"--csrf_token[ =\"]+([A-Za-z0-9-]{8,})", tail)
            ports = re.findall(r"127\.0\.0\.1:(\d{2,5})", tail)
            if csrf and ports:
                out.append({"port": int(ports[-1]), "csrf": csrf[-1], "seen": seen, "from": "log"})
    return out


def _processes() -> List[Tuple[int, str]]:
    """(pid, командная строка) процессов language_server*: psutil → PowerShell CIM → /proc."""
    try:
        import psutil  # type: ignore
        out = []
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            name = str(proc.info.get("name") or "").lower()
            if name.startswith("language_server"):
                out.append((int(proc.info["pid"]), " ".join(proc.info.get("cmdline") or [])))
        return out
    except ImportError:
        pass
    except Exception:  # noqa: BLE001
        return []
    if IS_WINDOWS:
        script = ("Get-CimInstance Win32_Process -Filter \"Name like 'language_server%'\" | "
                  "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress")
        try:
            raw = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                 capture_output=True, text=True, timeout=8,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
            data = json.loads(raw) if raw else []
            rows = data if isinstance(data, list) else [data]
            return [(int(r.get("ProcessId") or 0), str(r.get("CommandLine") or "")) for r in rows if isinstance(r, dict)]
        except Exception:  # noqa: BLE001
            return []
    out = []
    try:
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                cmd = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
            except OSError:
                continue
            if "language_server" in cmd.split(" ")[0]:
                out.append((int(entry.name), cmd))
    except OSError:
        pass
    return out


def _listening_ports() -> Dict[int, List[int]]:
    """pid → порты LISTEN: psutil → netstat -ano (Windows)."""
    out: Dict[int, List[int]] = {}
    try:
        import psutil  # type: ignore
        for conn in psutil.net_connections(kind="tcp"):
            if conn.status == "LISTEN" and conn.pid and conn.laddr:
                out.setdefault(conn.pid, []).append(conn.laddr.port)
        return out
    except ImportError:
        pass
    except Exception:  # noqa: BLE001
        return out
    if IS_WINDOWS:
        try:
            raw = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=8,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            for line in raw.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[3].upper() == "LISTENING" and parts[1].startswith("127.0.0.1:"):
                    out.setdefault(int(parts[4]), []).append(int(parts[1].rsplit(":", 1)[1]))
        except Exception:  # noqa: BLE001
            pass
    return out


def ag_from_processes() -> List[Dict[str, Any]]:
    procs = [(pid, cmd) for pid, cmd in _processes() if "--csrf_token" in cmd]
    if not procs:
        return []
    ports = _listening_ports()
    out = []
    for pid, cmd in sorted(procs, reverse=True):
        match = re.search(r"--csrf_token[ =\"]+([A-Za-z0-9-]{8,})", cmd)
        if not match:
            continue
        mine = sorted(set(ports.get(pid) or []))
        https = [p for p in mine if p + 1 in mine] or mine[:2]   # HTTPS — меньший из соседних портов
        for port in https[:2]:
            out.append({"port": port, "csrf": match.group(1), "seen": time.time(), "from": "process"})
    return out


_AG_LAST: Dict[str, Dict[str, Any]] = {}


def antigravity_endpoints(account: Dict[str, Any]) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    last = _AG_LAST.get(account.get("id") or "")
    if last:
        found.append(last)
    found += ag_from_processes()
    files = ag_from_daemon_files(_gemini_root(account))
    if (account.get("mode") or "auto") == "auto":
        files += ag_from_logs()
    found += sorted(files, key=lambda e: -e["seen"])
    out, keys = [], set()
    for ep in found:
        key = (ep["port"], ep["csrf"])
        if key not in keys:
            keys.add(key)
            out.append(ep)
    return out[:6]


async def fetch_antigravity(account: Dict[str, Any], secret: Optional[Dict[str, str]], lang: str, now: float) -> Dict[str, Any]:
    endpoints = await asyncio.to_thread(antigravity_endpoints, account)
    if not endpoints:
        raise UsageWall("source_gone:antigravity", "Antigravity не запущен — квоты живут в его локальном языковом сервере.")
    last = ""
    for ep in endpoints:
        base = f"https://127.0.0.1:{ep['port']}"
        headers = {"Content-Type": "application/json", "X-Codeium-Csrf-Token": ep["csrf"], "Connect-Protocol-Version": "1"}
        try:
            status, _h, text = await HTTP("POST", base + AG_STATUS, headers, AG_BODY, verify=False, timeout=5)
        except Exception as exc:  # noqa: BLE001
            last = scrub(exc)[:120]
            continue
        if status != 200:
            last = f"HTTP {status}"
            continue
        try:
            status_json = json.loads(text)
        except ValueError:
            last = "не JSON"
            continue
        summary = None
        try:
            s2, _h2, t2 = await HTTP("POST", base + AG_QUOTA, headers, AG_BODY, verify=False, timeout=5)
            if s2 == 200:
                summary = json.loads(t2)
        except Exception:  # noqa: BLE001 — старый сервер не знает сводку: останутся 5-часовые окна
            summary = None
        _AG_LAST[account.get("id") or ""] = ep
        user = (status_json or {}).get("userStatus") or {}
        tier = ((user.get("userTier") or {}).get("name")) or (((user.get("planStatus") or {}).get("planInfo") or {}).get("planName")) or ""
        return {"plan": str(tier), "via": "Antigravity", "limits": antigravity_limits(summary, status_json, lang, now)}
    raise UsageWall("source_gone:antigravity", f"Antigravity не отвечает ({last or 'нет ответа'}) — откройте приложение.")


FETCHERS = {"claude": fetch_claude, "codex": fetch_codex, "antigravity": fetch_antigravity}
from .panels_proxy_usage import fetch as fetch_proxy_usage
FETCHERS.update({key: fetch_proxy_usage for key in ("kiro", "proxy", "api_budget", "quota_planner")})


# ====================================================================== аккаунты и обновление панели

def new_account_id() -> str:
    return "a_" + uuid.uuid4().hex[:10]


def clean_accounts(raw: Any) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        provider = str(row.get("provider") or "")
        if provider not in PROVIDERS:
            continue
        aid = str(row.get("id") or "")
        if not re.fullmatch(r"a_[a-z0-9]{6,24}", aid) or aid in seen:
            aid = new_account_id()
        seen.add(aid)
        mode = str(row.get("mode") or "auto")
        out.append({"id": aid, "provider": provider, "mode": mode if mode in MODES[provider] else MODES[provider][0],
                    "name": re.sub(r"\s+", " ", str(row.get("name") or "")).strip()[:40],
                    "dir": str(row.get("dir") or "").strip()[:400],
                    "proxy_account_id": str(row.get("proxy_account_id") or "")[:96]})
        if len(out) >= MAX_ACCOUNTS:
            break
    return out


PROVIDER_NAMES = {"claude": "Claude", "codex": "Codex", "antigravity": "Antigravity", "kiro": "Kiro",
                  "proxy": "MemuAI Proxy", "api_budget": "API · USD", "quota_planner": "Quota planner"}


def _wall_text(wall: UsageWall, lang: str) -> str:
    return wall.message or wall.status


def source_identity(account: Dict[str, Any]) -> str:
    """Bind cached numbers/cooldowns to a selected source, never credentials."""
    fields = {key: str(account.get(key) or "") for key in ("provider", "mode", "dir", "proxy_account_id")}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


async def refresh_accounts(accounts: List[Dict[str, Any]], previous: List[Dict[str, Any]], *, lang: str = "ru",
                           now: Optional[float] = None, force: bool = False) -> Dict[str, Any]:
    """Снимок всех аккаунтов. Каждый — сам по себе: стена одного не мешает остальным."""
    now = time.time() if now is None else now
    prev = {str(p.get("id")): p for p in previous or [] if isinstance(p, dict)}

    async def one(account: Dict[str, Any]) -> Dict[str, Any]:
        old = prev.get(account["id"]) or {}
        identity = source_identity(account)
        if old.get("source_identity") != identity:
            # Editing a row can retain its id while replacing its account or
            # provider. Old limits and rate-limit cooldowns then belong to a
            # different source. Legacy snapshots without identity are refreshed.
            old = {}
        base = {"id": account["id"], "provider": account["provider"],
                "source_identity": identity, "checked": old.get("checked"),
                "name": account.get("name") or PROVIDER_NAMES[account["provider"]],
                "plan": old.get("plan") or "", "via": old.get("via") or "", "limits": old.get("limits") or [],
                "fetched": old.get("fetched"), "cooldown_until": old.get("cooldown_until"), "state": old.get("state") or "wall",
                "wall": old.get("wall"), "notice": old.get("notice") or ""}
        cooldown = float(old.get("cooldown_until") or 0)
        if cooldown > now:
            base["state"] = "stale" if base["limits"] else "wall"
            return base
        if not force and old.get("checked") and now - float(old["checked"]) < MIN_SPACING and old.get("state") == "ok":
            return base
        base["checked"] = now
        secret = await asyncio.to_thread(get_secret, account["id"]) if account.get("mode") == "token" else None
        try:
            snap = await asyncio.wait_for(FETCHERS[account["provider"]](account, secret, lang, now), 30)
        except UsageWall as wall:
            base["wall"] = {"status": wall.status, "message": _wall_text(wall, lang)}
            if wall.status == "blocked:rate-limit":
                base["cooldown_until"] = now + (wall.retry_after or COOLDOWN_DEFAULT)
            base["state"] = "stale" if base["limits"] and wall.status.startswith("blocked:") else "wall"
            log.info("usage: %s/%s → %s", account["provider"], account["id"], wall.status)
            return base
        except asyncio.TimeoutError:
            base["wall"] = {"status": "blocked:source-down", "message": "Сервис не ответил за 30 с"}
            base["state"] = "stale" if base["limits"] else "wall"
            return base
        except Exception as exc:  # noqa: BLE001
            base["wall"] = {"status": "blocked:source-down", "message": scrub(exc)[:200]}
            base["state"] = "stale" if base["limits"] else "wall"
            log.warning("usage: %s failed: %s", account["provider"], scrub(exc))
            return base
        stale = snap.get("stale") is True
        fetched = _ts(snap.get("fetched"))
        base.update({"plan": snap.get("plan") or "", "via": snap.get("via") or "", "limits": snap.get("limits") or [],
                     "fetched": min(now, fetched) if fetched else now, "cooldown_until": None,
                     "state": "stale" if stale else "ok", "wall": snap.get("wall") if stale else None,
                     "notice": snap.get("notice") or ""})
        if not base["limits"]:
            base["state"] = "wall"
            base["wall"] = {"status": "blocked:not-found", "message": "Сервис не вернул ни одного окна лимитов"}
        return base

    rows = await asyncio.gather(*(one(acc) for acc in accounts))
    top = None
    for row in rows:
        if row["state"] in ("ok", "stale"):
            for limit in row["limits"]:
                if top is None or limit["used"] > top[0]:
                    top = (limit["used"], row["name"], limit["title"])
    return {"accounts": list(rows), "top_pct": round(top[0]) if top else None,
            "top_label": (top[1] if top else ("Лимиты" if lang != "en" else "Limits")),
            "count": len(rows), "ok": sum(1 for r in rows if r["state"] == "ok")}


def detect() -> Dict[str, List[Dict[str, Any]]]:
    """Какие локальные источники есть на этом компьютере — без токенов."""
    out: Dict[str, List[Dict[str, Any]]] = {"claude": [], "codex": [], "antigravity": []}
    cli = claude_cli_token({"mode": "cli"})
    out["claude"].append({"mode": "cli", "found": bool(cli), "plan": claude_plan((cli or {}).get("plan"), (cli or {}).get("tier")),
                          "path": str(claude_dirs({"mode": "cli"})[-1])})
    desktop = any((d / "config.json").exists() and (d / "Local State").exists() for d in claude_desktop_dirs())
    out["claude"].append({"mode": "desktop", "found": desktop and IS_WINDOWS, "present": desktop, "windows_only": True})
    auth = codex_auth({"mode": "cli"}, None)
    out["codex"].append({"mode": "cli", "found": bool(auth and not auth.get("api_key_only")),
                         "api_key_only": bool(auth and auth.get("api_key_only")),
                         "plan": str((auth or {}).get("plan") or ""), "path": str(codex_dir({"mode": "cli"}))})
    try:
        endpoints = antigravity_endpoints({"mode": "auto", "id": "_detect"})
    except Exception:  # noqa: BLE001
        endpoints = []
    out["antigravity"].append({"mode": "auto", "found": bool(endpoints), "running": bool(endpoints),
                               "path": str(home() / ".gemini")})
    return out
