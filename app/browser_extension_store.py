"""Browser conversations have their own profile-scoped collection by default."""
from __future__ import annotations

from . import pgdata
from .storage import load_json_with_recovery, mutate_json


def _path(profile_id):
    return pgdata.MEMU_DIR / pgdata._profile_data_name("browser-chats.json", profile_id)


def browser_chats(profile_id=None):
    rows = load_json_with_recovery(_path(profile_id), [], validator=lambda value: isinstance(value, list))
    return [{**row, "browser_only": True, "visibility": "browser"} for row in rows
            if isinstance(row, dict) and row.get("id") and not row.get("temp")][:pgdata.CHATS_LIMIT]


def load_chats(profile_id=None, scope="all"):
    main = [{**row, "browser_only": False, "visibility": "main"} for row in pgdata.load_chats(profile_id)
            if scope != "browser" or row.get("origin") == "browser_extension"]
    browser = browser_chats(profile_id) if scope != "main" else []
    # Existing shared chats always win if an old import duplicated an ID.
    by_id = {str(row["id"]): row for row in browser}
    by_id.update((str(row["id"]), row) for row in main)
    return sorted(by_id.values(), key=lambda row: pgdata._finite_schedule_number(row.get("updated")) or 0, reverse=True)


def upsert_chat(chat, profile_id=None, *, save_to_main=False):
    if save_to_main:
        return pgdata.upsert_chat({**chat, "browser_only": False, "visibility": "main", "origin": "browser_extension"}, profile_id)
    incoming = {**chat, "browser_only": True, "visibility": "browser", "origin": "browser_extension"}
    chat_id = str(incoming["id"])

    def merge(raw):
        rows = [row for row in raw if isinstance(row, dict) and str(row.get("id")) != chat_id]
        return True, pgdata._cap_chats([*rows, incoming])
    return mutate_json(_path(profile_id), [], merge, backup=False,
                       validator=lambda value: isinstance(value, list), repair_invalid=True)


def patch_chat(chat_id, patch, profile_id=None):
    # Main chats keep their existing atomic desktop/Telegram merge semantics.
    if any(str(row.get("id")) == str(chat_id) for row in pgdata.load_chats(profile_id)):
        return pgdata.patch_chat(chat_id, patch, profile_id)
    saved = []

    def apply(raw):
        for index, item in enumerate(raw):
            if isinstance(item, dict) and str(item.get("id")) == str(chat_id):
                row = dict(item)
                if patch(row):
                    raw[index] = row
                    saved.append(row)
                    return True, raw
                break
        return False, raw
    mutate_json(_path(profile_id), [], apply, backup=False,
                validator=lambda value: isinstance(value, list), repair_invalid=True)
    return saved[0] if saved else None
