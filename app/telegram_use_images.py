"""Images attached to Telegram Use automations.

Storage model (documented in audit/fixlog-tg-images.md):

* Files live in the Telegram Use data dir, next to ``telegram_use.json``,
  in ``telegram_use_automation_images/``. The folder is content-addressed:
  each file is named ``<sha256>.<ext>``, so the same picture uploaded for
  several rules is stored once and a rule only keeps small refs
  (``{"hash", "name", "size", "ext"}``). Rule JSON never carries image bytes,
  so the config stays small and rules survive copy/export as long as the
  image folder travels with ``telegram_use.json`` (both live in the same
  data dir; a rule whose file is missing still fires — it just sends text).
* Type is checked by magic bytes, never by extension or MIME claim.
* Limits: 10 images per rule, 10 MiB per image (Telegram photo cap),
  50 MiB total per rule. A ref whose file is gone or oversized resolves
  to ``None`` and is skipped at send time, never a crash.
"""

from __future__ import annotations

import base64
import hashlib
import re
import time
from pathlib import Path

from . import telegram_use_automations as automations
from . import telegram_use_config as config

DIR_NAME = "telegram_use_automation_images"
_REF_RE = re.compile(r"^[0-9a-f]{64}\.(png|jpg|gif|webp)$")
# Files uploaded but never attached to a rule (wizard closed before save)
# get a grace period before prune() removes them.
PRUNE_GRACE_SECONDS = 24 * 3600

EXT_TO_MIME = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}


def dir() -> Path:
    """Folder holding the automation images (next to telegram_use.json)."""
    return config.path().parent / DIR_NAME


def sniff(data: bytes) -> str | None:
    """Image type from magic bytes, or None when the bytes are not an image."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def save_image(data: bytes, name: str = "") -> dict:
    """Store image bytes, return the ref dict kept in the rule.

    Raises ValueError for non-images and for files over the per-image cap.
    The same bytes always produce the same ref (content addressing).
    """
    if len(data) > automations.MAX_IMAGE_BYTES:
        raise ValueError(f"Изображение больше {automations.MAX_IMAGE_BYTES // (1024 * 1024)} МБ")
    ext = sniff(data)
    if ext is None:
        raise ValueError("Файл не является изображением (PNG, JPEG, GIF, WebP)")
    digest = hashlib.sha256(data).hexdigest()
    folder = dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{digest}.{ext}"
    if not path.exists():
        tmp = folder / f".{digest}.tmp"
        tmp.write_bytes(data)
        tmp.replace(path)
    return {"hash": digest, "name": (name or f"image.{ext}")[:120], "size": len(data), "ext": ext}


def save_base64(data_url: str, name: str = "") -> dict:
    """Decode a ``data:image/...;base64,...`` (or bare base64) payload and store it."""
    raw = (data_url or "").strip()
    if "," in raw and raw.startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        data = base64.b64decode(raw, validate=True)
    except Exception:
        raise ValueError("Некорректные данные изображения (base64)")
    return save_image(data, name)


def resolve(ref: dict | None) -> Path | None:
    """Return the file for a ref, or None when it is invalid or gone.

    A missing file is a normal case (user deleted the folder, rule copied
    without its images) — callers must send text-only, never fail.
    """
    if not isinstance(ref, dict):
        return None
    name = f"{ref.get('hash', '')}.{ref.get('ext', '')}"
    if not _REF_RE.match(name):
        return None
    try:
        path = (dir() / name).resolve()
        folder = dir().resolve()
    except Exception:
        return None
    if folder not in path.parents:
        return None
    if not path.is_file() or path.stat().st_size > automations.MAX_IMAGE_BYTES:
        return None
    return path


def resolve_by_name(name: str) -> Path | None:
    """Resolve a bare ``<sha256>.<ext>`` file name (API thumbnail route)."""
    if not isinstance(name, str) or not _REF_RE.match(name):
        return None
    return resolve({"hash": name[:64], "ext": name[65:]})


def resolve_many(refs) -> tuple[list[Path], int]:
    """Resolve a list of refs; returns (found files, missing count)."""
    found, missing = [], 0
    for ref in refs or []:
        path = resolve(ref)
        if path is None:
            missing += 1
        else:
            found.append(path)
    return found, missing


def used_hashes(rules) -> set[str]:
    """Hashes referenced by any rule's ``do.images`` (for pruning)."""
    out = set()
    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        do = rule.get("do") or {}
        for ref in do.get("images") or []:
            if isinstance(ref, dict) and isinstance(ref.get("hash"), str):
                out.add(ref["hash"])
    return out


def prune(used: set[str], *, grace_seconds: int = PRUNE_GRACE_SECONDS) -> int:
    """Delete image files no rule references. Returns how many were removed.

    Files newer than ``grace_seconds`` are kept: a picture uploaded in the
    wizard but not yet saved with its rule must not vanish under the user.
    Never raises — pruning is hygiene, not a reason to break a save.
    """
    try:
        folder = dir()
        if not folder.is_dir():
            return 0
        cutoff = time.time() - grace_seconds
        removed = 0
        for path in folder.iterdir():
            if not path.is_file() or not _REF_RE.match(path.name):
                continue
            if path.name[:64] in used or path.stat().st_mtime > cutoff:
                continue
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
        return removed
    except OSError:
        return 0
