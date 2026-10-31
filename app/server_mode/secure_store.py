"""Protected storage for the client-side server token.

The token lives encrypted, never in plain settings files and never in
logs.  Encryption reuses the project's existing protected-storage
primitives from :mod:`app.dfy_vault`:

* Windows — DPAPI (``CryptProtectData``) bound to the current user;
* other OS — the ``_FileKeyCipher`` fallback (random 256-bit key file,
  mode 0600, HMAC-SHA256 stream cipher with encrypt-then-MAC).

If the vault module cannot be imported at all, storing the token fails
loudly instead of silently writing it in plain text.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .auth import mask_token


class SecureStoreError(Exception):
    """The token cannot be stored or read through protected storage."""


class ClientSecretStore:
    """One encrypted value: the server token."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.key_path = self.path.with_name(self.path.name + ".key")

    def _cipher(self):
        try:
            from ..dfy_vault import _FileKeyCipher, _dpapi
        except Exception as exc:  # noqa: BLE001 - never store plain text
            raise SecureStoreError(f"protected storage is unavailable: {exc}") from exc
        if os.name == "nt":
            return ("dpapi", _dpapi, None)
        return ("filekey", None, _FileKeyCipher(self.key_path))

    def save_token(self, token: str) -> None:
        token = str(token or "")
        if not token:
            raise SecureStoreError("empty token")
        scheme, dpapi, cipher = self._cipher()
        if scheme == "dpapi":
            blob = dpapi(token.encode("utf-8"), protect=True)
        else:
            blob = cipher.encrypt(token.encode("utf-8"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(blob)
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def load_token(self) -> Optional[str]:
        try:
            blob = self.path.read_bytes()
        except OSError:
            return None
        if not blob:
            return None
        scheme, dpapi, cipher = self._cipher()
        try:
            if scheme == "dpapi":
                raw = dpapi(blob, protect=False)
            else:
                raw = cipher.decrypt(blob)
        except Exception as exc:  # noqa: BLE001 - corrupt vault entry
            raise SecureStoreError(f"stored token cannot be read: {exc}") from exc
        value = raw.decode("utf-8", "replace").strip()
        return value or None

    def clear(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass

    def has_token(self) -> bool:
        try:
            return self.load_token() is not None
        except SecureStoreError:
            return False

    def mask(self) -> str:
        try:
            token = self.load_token()
        except SecureStoreError:
            return ""
        return mask_token(token or "")
