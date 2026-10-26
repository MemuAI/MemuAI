"""Round 29 «Синхронизация»: encryption of backups and local storage of sync secrets.

Two independent jobs, both without third-party dependencies (the bundle has no
``cryptography`` package):

* **Backup encryption with a passphrase** (optional, chosen by the user).  The key
  is derived with scrypt (N=2^15, r=8, p=1, a per-repository random salt); the
  64-byte result is split into an encryption key and a MAC key.  Data is
  encrypted with a SHAKE-256 keystream (``SHAKE256(enc_key ‖ nonce ‖ block#)``,
  64 KiB per block — an extendable-output function used as a PRF in counter
  mode) and authenticated with HMAC-SHA256 over ``magic ‖ nonce ‖ ciphertext``
  (encrypt-then-MAC).  Files are processed as streams, so a 200 MB pack never
  sits in memory.  A wrong passphrase or a modified file fails the MAC check
  before a single plaintext byte is used.

* **Local secrets of the sync feature** (Google refresh token, the user's OAuth
  client secret, the stored passphrase for automatic encrypted backups).  On
  Windows they are protected with DPAPI for the current Windows user (the same
  primitive ``pgdata`` uses for profile PINs); elsewhere a random key file
  (mode 600) and the cipher above protect them from casual reading only.  The
  store lives in ``memuai/sync/`` which is never part of a backup.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any, BinaryIO, Dict, Optional

MAGIC = b"MSB1"
NONCE_BYTES = 16
TAG_BYTES = 32
BLOCK = 64 * 1024
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
CHECK_PLAINTEXT = b"memuai-sync-key-check-v1"

class CryptoError(ValueError):
    """Wrong passphrase, damaged file or an unknown format."""

    def __init__(self, ru: str, en: str = "") -> None:
        super().__init__(ru)
        self.ru = ru
        self.en = en or ru

class Keys:
    """Derived key material of one passphrase + salt."""

    __slots__ = ("enc", "mac", "key_id", "salt")

    def __init__(self, enc: bytes, mac: bytes, salt: bytes) -> None:
        self.enc = enc
        self.mac = mac
        self.salt = salt
        self.key_id = hashlib.sha256(b"memuai-sync-key-id|" + mac).hexdigest()[:16]

def derive_keys(passphrase: str, salt: bytes, *, n: int = SCRYPT_N, r: int = SCRYPT_R, p: int = SCRYPT_P) -> Keys:
    if not passphrase:
        raise CryptoError("Пустой пароль шифрования", "The encryption passphrase is empty")
    raw = hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                         maxmem=256 * 1024 * 1024, dklen=64)
    return Keys(raw[:32], raw[32:], salt)

def new_key_record(passphrase: str) -> tuple[Keys, Dict[str, Any]]:
    """A fresh salt for a (new) passphrase and the public record stored next to the backups."""
    salt = secrets.token_bytes(16)
    keys = derive_keys(passphrase, salt)
    record = {"key_id": keys.key_id, "kdf": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
              "salt": base64.b64encode(salt).decode("ascii"),
              "check": base64.b64encode(encrypt_bytes(CHECK_PLAINTEXT, keys)).decode("ascii")}
    return keys, record

def keys_from_record(passphrase: str, record: Dict[str, Any]) -> Keys:
    """Derive the keys of a stored record and prove the passphrase is right."""
    try:
        salt = base64.b64decode(str(record.get("salt") or ""))
        n, r, p = int(record.get("n") or SCRYPT_N), int(record.get("r") or SCRYPT_R), int(record.get("p") or SCRYPT_P)
    except (ValueError, TypeError) as exc:
        raise CryptoError("Повреждены сведения о ключе шифрования", "The encryption key record is damaged") from exc
    if not salt or n > 2 ** 20 or r > 32 or p > 16:
        raise CryptoError("Повреждены сведения о ключе шифрования", "The encryption key record is damaged")
    keys = derive_keys(passphrase, salt, n=n, r=r, p=p)
    check = record.get("check")
    if check:
        try:
            plain = decrypt_bytes(base64.b64decode(str(check)), keys)
        except CryptoError:
            plain = b""
        if plain != CHECK_PLAINTEXT:
            raise CryptoError("Неверный пароль шифрования", "Wrong encryption passphrase")
    return keys

def _xor(data: bytes, stream: bytes) -> bytes:
    n = len(data)
    if not n:
        return b""
    return (int.from_bytes(data, "little") ^ int.from_bytes(stream[:n], "little")).to_bytes(n, "little")

def _keystream_block(key: bytes, nonce: bytes, index: int, length: int) -> bytes:
    return hashlib.shake_256(b"memuai-sync|" + key + nonce + index.to_bytes(8, "big")).digest(length)

class _Stream:
    """Incremental encrypt/decrypt with a running MAC."""

    def __init__(self, keys: Keys, nonce: bytes) -> None:
        self.keys = keys
        self.nonce = nonce
        self.mac = hmac.new(keys.mac, MAGIC + nonce, hashlib.sha256)
        self.index = 0
        self.pending = b""

    def _blocks(self, data: bytes, *, final: bool, encrypt: bool) -> bytes:
        self.pending += data
        out = []
        while len(self.pending) >= BLOCK or (final and self.pending):
            chunk, self.pending = self.pending[:BLOCK], self.pending[BLOCK:]
            if not encrypt:
                self.mac.update(chunk)
            result = _xor(chunk, _keystream_block(self.keys.enc, self.nonce, self.index, len(chunk)))
            if encrypt:
                self.mac.update(result)
            self.index += 1
            out.append(result)
        return b"".join(out)

def encrypt_bytes(data: bytes, keys: Keys) -> bytes:
    nonce = secrets.token_bytes(NONCE_BYTES)
    stream = _Stream(keys, nonce)
    body = stream._blocks(data, final=True, encrypt=True)
    return MAGIC + nonce + stream.mac.digest() + body

def decrypt_bytes(blob: bytes, keys: Keys) -> bytes:
    head = len(MAGIC) + NONCE_BYTES + TAG_BYTES
    if not blob.startswith(MAGIC) or len(blob) < head:
        raise CryptoError("Файл резервной копии повреждён или не зашифрован",
                          "The backup file is damaged or not encrypted")
    nonce = blob[len(MAGIC):len(MAGIC) + NONCE_BYTES]
    tag = blob[len(MAGIC) + NONCE_BYTES:head]
    body = blob[head:]
    mac = hmac.new(keys.mac, MAGIC + nonce, hashlib.sha256)
    mac.update(body)
    if not hmac.compare_digest(mac.digest(), tag):
        raise CryptoError("Неверный пароль или файл повреждён", "Wrong passphrase or a damaged file")
    stream = _Stream(keys, nonce)
    stream.mac = hmac.new(b"x", digestmod=hashlib.sha256)
    return stream._blocks(body, final=True, encrypt=False)

def encrypt_file(src: Path, dst: Path, keys: Keys) -> int:
    """Encrypt ``src`` into ``dst``; returns the size of ``dst``."""
    nonce = secrets.token_bytes(NONCE_BYTES)
    stream = _Stream(keys, nonce)
    with open(src, "rb") as reader, open(dst, "wb") as writer:
        writer.write(MAGIC + nonce + b"\0" * TAG_BYTES)
        while True:
            chunk = reader.read(BLOCK * 16)
            if not chunk:
                break
            writer.write(stream._blocks(chunk, final=False, encrypt=True))
        writer.write(stream._blocks(b"", final=True, encrypt=True))
        writer.seek(len(MAGIC) + NONCE_BYTES)
        writer.write(stream.mac.digest())
    return dst.stat().st_size

def _verify_file(handle: BinaryIO, keys: Keys) -> bytes:
    head = handle.read(len(MAGIC) + NONCE_BYTES + TAG_BYTES)
    if not head.startswith(MAGIC) or len(head) < len(MAGIC) + NONCE_BYTES + TAG_BYTES:
        raise CryptoError("Файл резервной копии повреждён или не зашифрован",
                          "The backup file is damaged or not encrypted")
    nonce = head[len(MAGIC):len(MAGIC) + NONCE_BYTES]
    tag = head[len(MAGIC) + NONCE_BYTES:]
    mac = hmac.new(keys.mac, MAGIC + nonce, hashlib.sha256)
    while True:
        chunk = handle.read(BLOCK * 16)
        if not chunk:
            break
        mac.update(chunk)
    if not hmac.compare_digest(mac.digest(), tag):
        raise CryptoError("Неверный пароль или файл повреждён", "Wrong passphrase or a damaged file")
    return nonce

def decrypt_file(src: Path, dst: Path, keys: Keys) -> None:
    """Verify the MAC of ``src`` first, then decrypt it into ``dst``."""
    with open(src, "rb") as reader:
        nonce = _verify_file(reader, keys)
        reader.seek(len(MAGIC) + NONCE_BYTES + TAG_BYTES)
        stream = _Stream(keys, nonce)
        with open(dst, "wb") as writer:
            while True:
                chunk = reader.read(BLOCK * 16)
                if not chunk:
                    break
                writer.write(stream._blocks(chunk, final=False, encrypt=False))
            writer.write(stream._blocks(b"", final=True, encrypt=False))

def is_encrypted(blob_head: bytes) -> bool:
    return blob_head.startswith(MAGIC)

_DPAPI_PREFIX = "dpapi:v1:"
_FILE_PREFIX = "file:v1:"

def _dpapi(data: bytes, *, protect: bool) -> bytes:  # pragma: no cover - Windows only
    from .pgdata import _dpapi as pg_dpapi
    return pg_dpapi(data, protect=protect)

class SecretStore:
    """Small name → string store, protected for the current Windows user."""

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self.path = self.folder / "secrets.json"
        self._lock = threading.Lock()
        self._file_keys: Optional[Keys] = None

    @property
    def scheme(self) -> str:
        return "dpapi" if os.name == "nt" else "file-key"

    def _key(self) -> Keys:
        if self._file_keys is not None:
            return self._file_keys
        key_path = self.folder / "secrets.key"
        if key_path.is_file():
            raw = base64.b64decode(key_path.read_text("ascii").strip() or "")
        else:
            raw = secrets.token_bytes(64)
            self.folder.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="ascii") as handle:
                handle.write(base64.b64encode(raw).decode("ascii"))
        if len(raw) != 64:
            raise CryptoError("Ключ хранилища синхронизации повреждён", "The sync secret key is damaged")
        self._file_keys = Keys(raw[:32], raw[32:], b"")
        return self._file_keys

    def _protect(self, value: str) -> str:
        data = value.encode("utf-8")
        if os.name == "nt":
            return _DPAPI_PREFIX + base64.b64encode(_dpapi(data, protect=True)).decode("ascii")
        return _FILE_PREFIX + base64.b64encode(encrypt_bytes(data, self._key())).decode("ascii")

    def _unprotect(self, stored: str) -> str:
        if stored.startswith(_DPAPI_PREFIX):
            return _dpapi(base64.b64decode(stored[len(_DPAPI_PREFIX):]), protect=False).decode("utf-8")
        if stored.startswith(_FILE_PREFIX):
            return decrypt_bytes(base64.b64decode(stored[len(_FILE_PREFIX):]), self._key()).decode("utf-8")
        return ""

    def _read(self) -> Dict[str, str]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def _write(self, data: Dict[str, str]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"secrets.json.tmp-{os.getpid()}-{threading.get_ident()}")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.replace(tmp, self.path)

    def get(self, name: str) -> str:
        with self._lock:
            stored = self._read().get(name, "")
        if not stored:
            return ""
        try:
            return self._unprotect(stored)
        except Exception:

            return ""

    def has(self, name: str) -> bool:
        return bool(self.get(name))

    def set(self, name: str, value: str) -> None:
        with self._lock:
            data = self._read()
            if value:
                data[name] = self._protect(value)
            else:
                data.pop(name, None)
            self._write(data)

    def delete(self, name: str) -> None:
        self.set(name, "")
