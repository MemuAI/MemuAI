"""Server mode: bearer-token authentication for the MemuAI server.

One owner, no multi-user scopes: every ``/api/server/*`` request must carry
the single server token.  The token is generated on first server start and
stored only on the server (``server_token`` file, mode 0600, inside the
server data dir).  It is never logged and never sent back to any client;
clients only ever see a mask (``••••`` + last 4 chars).

Comparison is constant-time (``hmac.compare_digest``).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path
from typing import Optional

TOKEN_FILENAME = "server_token"


def mask_token(token: str) -> str:
    """Masked form safe to show in UI/logs: ``••••`` + last 4 characters."""
    token = str(token or "")
    if len(token) <= 4:
        return "••••" if token else ""
    return "••••" + token[-4:]


class TokenStore:
    """The server-side token: generated once, read back, verified."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / TOKEN_FILENAME

    def load_existing(self) -> Optional[str]:
        try:
            value = self.path.read_text("utf-8").strip()
        except OSError:
            return None
        return value or None

    def get_or_create(self) -> str:
        existing = self.load_existing()
        if existing:
            return existing
        token = secrets.token_urlsafe(32)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(token + "\n")
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
        return token

    def verify(self, presented: Optional[str]) -> bool:
        """Constant-time check. Empty / missing credentials never match."""
        if not presented:
            return False
        expected = self.load_existing()
        if not expected:
            return False
        # Compare SHA-256 digests so timing does not leak the token length.
        presented_digest = hashlib.sha256(str(presented).encode("utf-8")).digest()
        expected_digest = hashlib.sha256(expected.encode("utf-8")).digest()
        return hmac.compare_digest(presented_digest, expected_digest)


def bearer_token_from_header(authorization: Optional[str]) -> Optional[str]:
    """Extract the token from ``Authorization: Bearer <token>`` or None."""
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) != 2 or parts[0] != "Bearer":
        return None
    return parts[1] or None
