"""Round 35 (branches): chat branches and answer versions — the Python mirror of ``pgstatic/branches.js``.

Model (backward compatible):

* ``chat["messages"]`` stays a LINEAR list and is the ACTIVE PATH (the active branch). Everything that reads it
  (model context, compression, token counting, export, Telegram, the Mini App, search) sees the active branch only.
  Old chats are simply one branch; there is no migration.
* ``chat["branches"] = {"v": 1, "forks": {key: {"sel": int, "alts": [{"id", "at", "m"}, ...]}}}`` is optional.
  ``key`` is the id of the message AFTER which the fork happens (``"^"`` = the very start). ``alts`` are the
  versions of what follows ``key``; ``alts[sel]["m"] is None`` (the selected version lives in the sequence that
  holds ``key``), every other version keeps its whole continuation in ``m``. Nested forks inside an inactive
  version use the same scheme. Message ids (``m["id"]``) are handed out lazily.

All functions mutate the given chat dict in place (callers run them inside ``pgdata.patch_chat``).
"""
from __future__ import annotations

import secrets
import time
from typing import Any, Dict, Iterator, List, Optional

ROOT = "^"
MAX_ALTS = 60


def uid(prefix: str) -> str:
    return f"{prefix}{int(time.time() * 1000):x}{secrets.token_hex(3)}"


def msg_id(message: Dict[str, Any]) -> str:
    """The message's stable id (assigned on first use)."""
    if isinstance(message, dict) and not message.get("id"):
        message["id"] = uid("m-")
    return str(message.get("id") or "") if isinstance(message, dict) else ""


def active_path(chat: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The messages of the active branch (what the user sees). Same list object as ``chat['messages']``."""
    rows = chat.get("messages") if isinstance(chat, dict) else None
    return rows if isinstance(rows, list) else []


def forks_of(chat: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    branches = chat.get("branches") if isinstance(chat, dict) else None
    forks = branches.get("forks") if isinstance(branches, dict) else None
    return forks if isinstance(forks, dict) else None


def _ensure_store(chat: Dict[str, Any]) -> Dict[str, Any]:
    if forks_of(chat) is None:
        chat["branches"] = {"v": 1, "forks": {}}
    return chat["branches"]["forks"]


def _index_map(messages: List[Dict[str, Any]]) -> Dict[str, int]:
    return {str(m["id"]): i for i, m in enumerate(messages) if isinstance(m, dict) and m.get("id")}


def _pos_of_key(index: Dict[str, int], key: str) -> Optional[int]:
    if key == ROOT:
        return -1
    return index.get(key)


def key_at(messages: List[Dict[str, Any]], pos: int) -> str:
    return ROOT if pos <= 0 else msg_id(messages[pos - 1])


def begin_version(chat: Dict[str, Any], pos: int) -> str:
    """Keep ``messages[pos:]`` as an inactive version and start a new, empty active one (truncates messages)."""
    messages = active_path(chat)
    pos = max(0, min(len(messages), int(pos)))
    suffix = messages[pos:]
    if not any(isinstance(m, dict) and not m.get("internal") for m in suffix):
        return ""
    key = key_at(messages, pos)
    for row in suffix:
        msg_id(row)
    forks = _ensure_store(chat)
    fork = forks.get(key)
    if not isinstance(fork, dict) or not isinstance(fork.get("alts"), list) or not fork["alts"]:
        at = suffix[0].get("createdAt") if isinstance(suffix[0], dict) else None
        fork = forks[key] = {"sel": 0, "alts": [{"id": uid("v-"), "at": int(at or time.time() * 1000), "m": None}]}
    sel = max(0, min(len(fork["alts"]) - 1, int(fork.get("sel") or 0)))
    fork["alts"][sel]["m"] = suffix
    fork["alts"].append({"id": uid("v-"), "at": int(time.time() * 1000), "m": None})
    fork["sel"] = len(fork["alts"]) - 1
    while len(fork["alts"]) > MAX_ALTS:
        fork["alts"].pop(0)
        fork["sel"] -= 1
    chat["messages"] = messages[:pos]
    return key


def switch_to(chat: Dict[str, Any], key: str, j: int) -> bool:
    forks = forks_of(chat) or {}
    fork = forks.get(key)
    if not isinstance(fork, dict) or not isinstance(fork.get("alts"), list):
        return False
    messages = active_path(chat)
    p = _pos_of_key(_index_map(messages), key)
    sel = int(fork.get("sel") or 0)
    if p is None or j < 0 or j >= len(fork["alts"]) or j == sel:
        return False
    nxt = fork["alts"][j].get("m")
    nxt = nxt if isinstance(nxt, list) else []
    fork["alts"][sel]["m"] = messages[p + 1:]
    fork["alts"][j]["m"] = None
    fork["sel"] = j
    chat["messages"] = messages[:p + 1] + nxt
    return True


def locate(chat: Dict[str, Any], message_id: str) -> Optional[Dict[str, Any]]:
    for key, fork in (forks_of(chat) or {}).items():
        for a, alt in enumerate(fork.get("alts") or []):
            rows = alt.get("m") if isinstance(alt, dict) else None
            if isinstance(rows, list):
                for k, row in enumerate(rows):
                    if isinstance(row, dict) and row.get("id") == message_id:
                        return {"key": key, "alt": a, "offset": k}
    return None


def reveal(chat: Dict[str, Any], message_id: str, depth: int = 0) -> bool:
    """Make the branch holding ``message_id`` active."""
    if depth > 200 or not message_id:
        return False
    if any(isinstance(m, dict) and m.get("id") == message_id for m in active_path(chat)):
        return True
    loc = locate(chat, message_id)
    if not loc:
        return False
    if loc["key"] != ROOT and not any(isinstance(m, dict) and m.get("id") == loc["key"] for m in active_path(chat)):
        if not reveal(chat, loc["key"], depth + 1):
            return False
    return switch_to(chat, loc["key"], loc["alt"])


def info(chat: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    """Versions on the active path: ``{message index: {"key", "n", "cur"}}`` (cur is 0-based)."""
    out: Dict[int, Dict[str, Any]] = {}
    forks = forks_of(chat)
    if not forks:
        return out
    messages = active_path(chat)
    index = _index_map(messages)
    for key, fork in forks.items():
        alts = fork.get("alts") if isinstance(fork, dict) else None
        if not isinstance(alts, list) or len(alts) < 2:
            continue
        p = _pos_of_key(index, key)
        if p is None or p + 1 >= len(messages):
            continue
        out[p + 1] = {"key": key, "n": len(alts), "cur": int(fork.get("sel") or 0)}
    return out


def stats(chat: Dict[str, Any]) -> Dict[str, int]:
    res = {"forks": 0, "versions": 0, "hidden": 0}
    for fork in (forks_of(chat) or {}).values():
        alts = fork.get("alts") if isinstance(fork, dict) else None
        if not isinstance(alts, list) or len(alts) < 2:
            continue
        res["forks"] += 1
        res["versions"] += len(alts) - 1
        res["hidden"] += sum(len(a["m"]) for a in alts if isinstance(a, dict) and isinstance(a.get("m"), list))
    return res


def inactive_messages(chat: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    """Messages of every NON-active version: ``{"m", "key", "alt", "offset"}`` (for search)."""
    for key, fork in (forks_of(chat) or {}).items():
        for a, alt in enumerate(fork.get("alts") or []):
            rows = alt.get("m") if isinstance(alt, dict) else None
            if isinstance(rows, list):
                for k, row in enumerate(rows):
                    if isinstance(row, dict):
                        yield {"m": row, "key": key, "alt": a, "offset": k}


def normalize(chat: Dict[str, Any]) -> bool:
    """Drop orphaned forks, empty versions and repair ``sel``. Returns True when something changed."""
    forks = forks_of(chat)
    if forks is None:
        if isinstance(chat, dict) and chat.get("branches") is not None:
            chat.pop("branches", None)
            return True
        return False
    changed = False
    for _ in range(4):
        ids = {str(m["id"]) for m in active_path(chat) if isinstance(m, dict) and m.get("id")}
        for fork in forks.values():
            for alt in (fork.get("alts") if isinstance(fork, dict) else None) or []:
                if isinstance(alt, dict) and isinstance(alt.get("m"), list):
                    ids.update(str(m["id"]) for m in alt["m"] if isinstance(m, dict) and m.get("id"))
        index = _index_map(active_path(chat))
        again = False
        for key in list(forks):
            fork = forks[key]
            if not isinstance(fork, dict) or not isinstance(fork.get("alts"), list) or (key != ROOT and key not in ids):
                del forks[key]
                changed = again = True
                continue
            sel = max(0, min(len(fork["alts"]) - 1, int(fork.get("sel") or 0)))
            if sel != fork.get("sel"):
                changed = True
            kept = []
            for i, alt in enumerate(fork["alts"]):
                ok = isinstance(alt, dict) and (i == sel or (isinstance(alt.get("m"), list) and any(
                    isinstance(m, dict) and not m.get("internal") for m in alt["m"])))
                if ok:
                    kept.append(alt)
                else:
                    changed = True
                    if i < sel:
                        sel -= 1
            fork["alts"] = kept
            if not kept:
                del forks[key]
                changed = again = True
                continue
            sel = max(0, min(len(kept) - 1, sel))
            fork["sel"] = sel
            if kept[sel].get("m") is not None:
                kept[sel]["m"] = None
                changed = True
            p = _pos_of_key(index, key)
            if p is not None and p + 1 >= len(active_path(chat)) and len(kept) > 1:
                kept.pop(sel)
                j = min(sel, len(kept) - 1)
                chat["messages"] = active_path(chat) + list(kept[j].get("m") or [])
                kept[j]["m"] = None
                fork["sel"] = j
                changed = again = True
            if len(fork["alts"]) < 2:
                del forks[key]
                changed = again = True
        if not again:
            break
    if not forks:
        chat.pop("branches", None)
        changed = True
    return changed


def version_note(chat: Dict[str, Any], index: int, lang: str = "ru") -> str:
    """«Версия 2/3» for message ``index`` on the active path, or ''."""
    it = info(chat).get(index)
    if not it:
        return ""
    return (f"Version {it['cur'] + 1}/{it['n']}" if lang == "en" else f"Версия {it['cur'] + 1}/{it['n']}")


def fork_off(chat: Dict[str, Any], upto_index: int, new_id: str, title: str) -> Dict[str, Any]:
    """A NEW chat with the active history up to and including ``upto_index`` (one linear branch)."""
    import copy
    rows = copy.deepcopy(active_path(chat)[:max(0, int(upto_index)) + 1])
    out = {k: copy.deepcopy(v) for k, v in chat.items()
           if k not in ("messages", "branches", "id", "title", "pinned", "unread", "folder", "contextSummary",
                        "contextSummaryThrough", "telegram", "_new")}
    out.update({"id": new_id, "title": title, "messages": rows, "parent_id": str(chat.get("id") or ""),
                "updated": int(time.time() * 1000), "pinned": False})
    through = int(chat.get("contextSummaryThrough") or 0)
    if through and through <= len(rows):
        out["contextSummary"] = chat.get("contextSummary") or ""
        out["contextSummaryThrough"] = through
    return out
