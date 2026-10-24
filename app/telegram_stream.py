"""Live streaming of model answers into Telegram.

Three modes (``telegram_stream_mode``):

* ``draft`` — Bot API 9.3+/9.5 ``sendMessageDraft``: the answer grows in an
  animated draft bubble with Telegram's own "stop" button (``can_stop``); the
  final text is then sent with ``sendMessage`` (drafts are only a 30-second
  preview, so a quiet model gets a keep-alive frame every 20 s);
* ``edit`` — a placeholder message edited with ``editMessageText`` and an
  inline ⏹ button (for clients/servers without drafts);
* ``off`` — "typing…" only, the whole answer at the end.

Frames are throttled (≈0.8 s for drafts, ≈1.2 s for edits — Telegram allows
about one message per second per chat) and every 429 ``retry_after`` pushes
the next frame back and slows the pace. A frame is cosmetic: a failed frame is
skipped, never retried, and never loses the answer. An answer longer than one
message is committed in parts at paragraph boundaries while streaming.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Callable, Dict, List, Optional

from .telegram_api import BotApi, TelegramError, redact
from .telegram_format import (html_to_plain, markdown_to_telegram_html, render_partial,
                              split_markdown_source, split_telegram_html, utf16_len)

log = logging.getLogger("memuai.telegram")

SOURCE_LIMIT = 3300          # Markdown characters per streamed message
DRAFT_INTERVAL = 0.8
EDIT_INTERVAL = 1.2
DRAFT_KEEPALIVE = 20.0
TYPING_INTERVAL = 4.5
_DRAFTS_UNSUPPORTED = {"value": False}


# Round 24: chat id → monotonic deadline. While it lasts, streaming frames of
# that chat pause, so the per-chat message budget (≈1 edit/s) goes to the menu
# edit of a pressed button instead of a 429 for it.
_USER_PRIORITY: Dict[str, float] = {}


def yield_to_user(chat_id: Any, seconds: float = 1.5) -> None:
    _USER_PRIORITY[str(chat_id)] = time.monotonic() + max(0.0, float(seconds))
    if len(_USER_PRIORITY) > 256:
        now = time.monotonic()
        for key in [k for k, v in _USER_PRIORITY.items() if v < now]:
            _USER_PRIORITY.pop(key, None)


def new_draft_id() -> int:
    return random.randint(1, 2_000_000_000)


class StreamReply:
    def __init__(self, api: BotApi, chat_id: str, *, mode: str = "draft", reply_to: int = 0,
                 stop_markup: Optional[Dict[str, Any]] = None,
                 placeholder: str = "…", on_draft_id: Optional[Callable[[int], None]] = None,
                 prepare: Optional[Callable[[str], str]] = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if mode == "draft" and _DRAFTS_UNSUPPORTED["value"]:
            mode = "edit"
        self.api = api
        self.chat_id = chat_id
        self.mode = mode if mode in ("draft", "edit", "off") else "draft"
        self.reply_to = int(reply_to or 0)
        self.stop_markup = stop_markup
        self.placeholder = placeholder
        self.on_draft_id = on_draft_id
        self.clock = clock
        # Round 26: cleaning of the raw answer (control blocks …) runs once per
        # frame, not once per streamed token — per token it cost O(answer
        # length) on the event loop, i.e. quadratic time for a long answer.
        self.prepare = prepare
        self.draft_id = new_draft_id()
        self._parts: List[str] = []    # raw answer: joined text + pieces since the last frame
        self.text = ""                 # full Markdown so far (prepared)
        self.offset = 0                # chars of ``text`` already committed
        self.carry = ""                # reopened code fence for the next part
        self.message_id = 0            # edit-mode message being edited
        self.sent_ids: List[int] = []  # permanent messages (committed parts + final)
        self.interval = DRAFT_INTERVAL if self.mode == "draft" else EDIT_INTERVAL
        self.next_frame = 0.0
        self.last_frame = 0.0
        self.last_typing = 0.0
        self.last_html = ""
        self.frames = 0
        self.dirty = False
        self.closed = False
        self._lock = asyncio.Lock()
        self._ticker: Optional[asyncio.Task] = None
        if on_draft_id:
            on_draft_id(self.draft_id)

    # ----------------------------------------------------------- lifecycle

    async def start(self) -> None:
        if self.mode == "draft":
            # Empty text shows Telegram's own "Thinking…" placeholder.
            await self._draft("")
        elif self.mode == "edit":
            await self._placeholder()
        else:
            await self._typing()
        self._ticker = asyncio.create_task(self._tick())

    def update(self, text: str) -> None:
        """Record the whole answer so far; the ticker decides when to show it."""
        if [text] != self._parts:
            self._parts = [text]
            self.dirty = True

    def append(self, delta: str) -> None:
        """Add a streamed piece in O(1); joining and ``prepare`` wait for the next frame."""
        if delta:
            self._parts.append(delta)
            self.dirty = True

    def _sync(self) -> None:
        source = "".join(self._parts)
        self._parts = [source] if source else []
        self.text = self.prepare(source) if self.prepare else source

    async def _tick(self) -> None:
        try:
            while not self.closed:
                await asyncio.sleep(0.25)
                if time.monotonic() < _USER_PRIORITY.get(str(self.chat_id), 0.0):
                    continue    # Round 24: a button was just pressed in this chat — let its screen through first
                now = self.clock()
                if self.dirty and now >= self.next_frame:
                    await self.flush()
                elif self.mode == "draft" and now - self.last_frame > DRAFT_KEEPALIVE \
                        and now >= self.next_frame:
                    await self.flush(keepalive=True)
                if self.mode != "draft" and not self.text and now - self.last_typing > TYPING_INTERVAL:
                    await self._typing()
        except asyncio.CancelledError:
            pass
        except Exception as exc:   # a frame must never kill the answer
            log.info("telegram stream ticker stopped: %s", redact(exc))

    async def _stop_ticker(self) -> None:
        self.closed = True
        if self._ticker:
            self._ticker.cancel()
            try:
                await self._ticker
            except (asyncio.CancelledError, Exception):
                pass

    # -------------------------------------------------------------- frames

    def _pending(self) -> str:
        return self.carry + self.text[self.offset:]

    async def flush(self, keepalive: bool = False) -> None:
        async with self._lock:
            if self.closed and not keepalive:
                return
            self.dirty = False
            self._sync()
            await self._commit_overflow()
            pending = self._pending()
            if self.mode == "off":
                return
            html = render_partial(pending) if pending.strip() else ""
            if utf16_len(html) > 4000:
                html = split_telegram_html(html, 3900)[0]
            if html == self.last_html and not keepalive:
                return
            ok = await (self._draft(html) if self.mode == "draft" else self._edit(html))
            if ok:
                self.last_html = html

    async def _commit_overflow(self) -> None:
        """Send finished parts of a long answer as permanent messages."""
        while True:
            pending = self._pending()
            if len(pending) <= SOURCE_LIMIT * 0.6:
                return      # cannot overflow yet: skip the extra render every frame
            limit = SOURCE_LIMIT
            if utf16_len(render_partial(pending[:SOURCE_LIMIT + 200])) > 3900:
                limit = int(SOURCE_LIMIT * 0.6)   # markup-heavy text renders longer
            head, carry, consumed = split_markdown_source(pending, limit)
            if not consumed:
                return
            # ``consumed`` counts the carried prefix too.
            used = max(0, consumed - len(self.carry))
            await self._send_final_part(head, markup=None, first=not self.sent_ids)
            self.offset += used
            self.carry = carry
            self.last_html = ""
            if self.mode == "draft":
                self.draft_id = new_draft_id()
                if self.on_draft_id:
                    self.on_draft_id(self.draft_id)
            elif self.mode == "edit":
                await self._placeholder()

    def _defer(self, error: Optional[TelegramError] = None) -> None:
        now = self.clock()
        if error is not None and error.error_code == 429:
            self.interval = min(5.0, self.interval * 1.6)
            self.next_frame = now + max(error.retry_after, self.interval)
        else:
            self.next_frame = now + self.interval

    async def _draft(self, html: str) -> bool:
        payload: Dict[str, Any] = {"chat_id": self.chat_id, "draft_id": self.draft_id,
                                   "text": html, "can_stop": True}
        if html:
            payload["parse_mode"] = "HTML"
        try:
            await self.api.call("sendMessageDraft", http_timeout=15, **payload)
        except TelegramError as error:
            if error.parse_error and html:
                try:
                    payload.pop("parse_mode", None)
                    payload["text"] = html_to_plain(html)[:4000]
                    await self.api.call("sendMessageDraft", http_timeout=15, **payload)
                except TelegramError:
                    pass
            elif error.error_code == 429:
                self._defer(error)
                return False
            elif error.unknown_method or error.error_code in (400, 403, 404):
                # Old Bot API server or drafts not allowed: edit messages instead.
                log.info("telegram drafts unavailable (%s); using message edits", error.description[:80])
                _DRAFTS_UNSUPPORTED["value"] = error.unknown_method or "draft" in error.description.lower()
                self.mode = "edit"
                self.interval = EDIT_INTERVAL
                await self._placeholder()
                return await self._edit(html) if html else True
            else:
                self._defer()
                return False
        except Exception as exc:
            log.info("telegram draft frame failed: %s", redact(exc))
            self._defer()
            return False
        self.frames += 1
        self.last_frame = self.clock()
        self._defer()
        return True

    async def _placeholder(self) -> None:
        payload: Dict[str, Any] = {"chat_id": self.chat_id, "text": self.placeholder,
                                   "link_preview_options": {"is_disabled": True}}
        if self.stop_markup:
            payload["reply_markup"] = self.stop_markup
        if self.reply_to and not self.sent_ids:
            payload["reply_parameters"] = {"message_id": self.reply_to,
                                           "allow_sending_without_reply": True}
        try:
            result = await self.api.call("sendMessage", essential=True, **payload)
            self.message_id = int((result or {}).get("message_id") or 0)
            self.last_frame = self.clock()
            self._defer()
        except Exception as exc:
            log.info("telegram placeholder failed: %s", redact(exc))
            self.message_id = 0

    async def _edit(self, html: str) -> bool:
        if not self.message_id or not html:
            return False
        payload: Dict[str, Any] = {"chat_id": self.chat_id, "message_id": self.message_id,
                                   "text": html, "parse_mode": "HTML",
                                   "link_preview_options": {"is_disabled": True}}
        if self.stop_markup:
            payload["reply_markup"] = self.stop_markup
        try:
            await self.api.call("editMessageText", http_timeout=15, **payload)
        except TelegramError as error:
            if error.not_modified:
                self._defer()
                return True
            if error.parse_error:
                payload.pop("parse_mode", None)
                payload["text"] = html_to_plain(html)[:4000]
                try:
                    await self.api.call("editMessageText", http_timeout=15, **payload)
                except TelegramError as second:
                    self._defer(second)
                    return False
            else:
                self._defer(error)
                return False
        except Exception as exc:
            log.info("telegram edit frame failed: %s", redact(exc))
            self._defer()
            return False
        self.frames += 1
        self.last_frame = self.clock()
        self._defer()
        return True

    async def _typing(self) -> None:
        self.last_typing = self.clock()
        try:
            await self.api.call("sendChatAction", http_timeout=10, chat_id=self.chat_id, action="typing")
        except Exception:
            pass

    # --------------------------------------------------------------- final

    async def _send_html(self, html: str, markup: Optional[Dict[str, Any]], *, reply: bool,
                         edit_id: int = 0) -> int:
        payload: Dict[str, Any] = {"chat_id": self.chat_id, "text": html, "parse_mode": "HTML",
                                   "link_preview_options": {"is_disabled": True}}
        if markup:
            payload["reply_markup"] = markup
        if edit_id:
            method = "editMessageText"
            payload["message_id"] = edit_id
            if not markup:
                payload["reply_markup"] = {"inline_keyboard": []}
        else:
            method = "sendMessage"
            if reply and self.reply_to:
                payload["reply_parameters"] = {"message_id": self.reply_to,
                                               "allow_sending_without_reply": True}
        for attempt in range(2):
            try:
                result = await self.api.call(method, essential=True, **payload)
                return int((result or {}).get("message_id") or edit_id or 0) if isinstance(result, dict) else edit_id
            except TelegramError as error:
                if error.not_modified:
                    return edit_id
                if attempt == 0 and error.parse_error:
                    # Never lose an answer because of a formatting edge case.
                    payload.pop("parse_mode", None)
                    payload["text"] = html_to_plain(html)[:4096]
                    continue
                if edit_id and attempt == 0:
                    # The placeholder vanished (deleted by the user): send anew.
                    method = "sendMessage"
                    payload.pop("message_id", None)
                    edit_id = 0
                    continue
                raise
        return 0

    async def _send_final_part(self, markdown: str, markup: Optional[Dict[str, Any]],
                               first: bool) -> None:
        chunks = split_telegram_html(markdown_to_telegram_html(markdown))
        for index, chunk in enumerate(chunks):
            last = index == len(chunks) - 1
            edit_id = self.message_id if (self.mode == "edit" and index == 0 and self.message_id) else 0
            message_id = await self._send_html(chunk, markup if last else None,
                                               reply=first and index == 0, edit_id=edit_id)
            if edit_id:
                self.message_id = 0
            if message_id:
                self.sent_ids.append(message_id)

    async def finish(self, text: str, markup: Optional[Dict[str, Any]] = None) -> List[int]:
        """Send the final answer (the rest of it, if parts were committed)."""
        await self._stop_ticker()
        async with self._lock:
            # Committed parts are final; only the tail can still change
            # (e.g. internal blocks removed from the finished text).
            tail = (self.carry + text[self.offset:]) if self.offset else text
            self.text = text
            self._parts = [text]
            if not tail.strip():
                tail = "…"
            await self._send_final_part(tail, markup, first=not self.sent_ids)
        return self.sent_ids

    async def fail(self, text: str, markup: Optional[Dict[str, Any]] = None) -> None:
        """Replace the stream with an error notice (plain text)."""
        await self._stop_ticker()
        async with self._lock:
            payload: Dict[str, Any] = {"chat_id": self.chat_id, "text": text}
            if markup:
                payload["reply_markup"] = markup
            try:
                if self.mode == "edit" and self.message_id:
                    await self.api.call("editMessageText", essential=True,
                                        message_id=self.message_id, **payload)
                else:
                    if self.reply_to and not self.sent_ids:
                        payload["reply_parameters"] = {"message_id": self.reply_to,
                                                       "allow_sending_without_reply": True}
                    await self.api.call("sendMessage", essential=True, **payload)
            except Exception as exc:
                log.warning("telegram error notice failed: %s", redact(exc))
