"""Round 36 (perf-core-2): a light chat list and per-chat deltas for ``/api/pg/chats``.

The window used to download the whole history of every chat (megabytes) each
time the chats file changed — Telegram, a schedule, another window.  Now it
asks for ``?view=meta``: one short row per chat with a content fingerprint
(``sig``), and then fetches only the chats whose fingerprint differs
(``?ids=a,b``).  The full list (no parameters) is unchanged for older callers.

The rows of one revision are fingerprinted once and kept until the file
changes, so repeated polls of the same revision cost nothing.
"""
from __future__ import annotations

import hashlib
import json
import threading
from typing import Any, Dict, List, Optional

#: Short text shown under a chat title; the window never needs more.
PREVIEW_CHARS = 120
#: A light row never carries more than this many ids in one request.
MAX_IDS = 200

_lock = threading.Lock()
# (profile key, revision) → {"meta": [...], "sigs": {id: sig}}
_cache: Dict[str, Dict[str, Any]] = {}


def chat_sig(row: Dict[str, Any]) -> str:
    """Fingerprint of a stored chat row: changes with any byte of it."""
    try:
        raw = json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "surrogatepass")
    except (TypeError, ValueError):
        raw = repr(row).encode("utf-8", "replace")
    return hashlib.blake2b(raw, digest_size=8).hexdigest()


def _preview(messages: List[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") in ("user", "assistant"):
            text = " ".join(str(message.get("content") or "").split())
            if text:
                return text[:PREVIEW_CHARS]
    return ""


#: Metadata the sidebar, projects and folders read; everything but the history.
META_KEYS = ("title", "updated", "created", "pinned", "mode", "folder_id", "project_id",
             "unread", "live", "titleAuto", "temp")


def chat_meta(row: Dict[str, Any], sig: Optional[str] = None) -> Dict[str, Any]:
    messages = row.get("messages") if isinstance(row.get("messages"), list) else []
    meta: Dict[str, Any] = {"id": str(row.get("id")), "sig": sig or chat_sig(row)}
    for key in META_KEYS:
        if key in row:
            meta[key] = row[key]
    meta["count"] = len(messages)
    meta["preview"] = _preview(messages)
    return meta


def _entry(profile_key: str, rev: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    key = f"{profile_key}\n{rev}"
    with _lock:
        hit = _cache.get(key)
    if hit is not None:
        return hit
    sigs: Dict[str, str] = {}
    meta: List[Dict[str, Any]] = []
    for row in rows:
        sig = chat_sig(row)
        sigs[str(row.get("id"))] = sig
        meta.append(chat_meta(row, sig))
    entry = {"meta": meta, "sigs": sigs}
    with _lock:
        # One revision per profile is enough: the window always asks for the newest.
        for old in [k for k in _cache if k.split("\n", 1)[0] == profile_key]:
            _cache.pop(old, None)
        _cache[key] = entry
    return entry


def cached_meta(profile_key: str, rev: str) -> Optional[List[Dict[str, Any]]]:
    """The light list of this revision when it was already built (no file read)."""
    with _lock:
        hit = _cache.get(f"{profile_key}\n{rev}")
    return hit["meta"] if hit is not None else None


def sigs_for(profile_key: str, rev: str, rows: List[Dict[str, Any]]) -> Dict[str, str]:
    return _entry(profile_key, rev, rows)["sigs"]


def meta_for(profile_key: str, rev: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return _entry(profile_key, rev, rows)["meta"]


def parse_ids(ids: str = "", single: str = "") -> List[str]:
    out: List[str] = []
    for value in [single, *str(ids or "").split(",")]:
        value = str(value or "").strip()
        if value and value not in out:
            out.append(value)
    return out[:MAX_IDS]


def pick(rows: List[Dict[str, Any]], wanted: List[str]) -> List[Dict[str, Any]]:
    want = set(wanted)
    return [row for row in rows if str(row.get("id")) in want]
