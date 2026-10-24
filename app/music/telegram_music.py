# -*- coding: utf-8 -*-
"""Round 37: MemuMusic from the Telegram bot.

* ``/music <ссылка>`` — the computer downloads a track / album / playlist into MemuMusic; one bot message shows the
  progress (edited, at most every few seconds) and the result with a button that opens «Музыка» in the Mini App.
* A message that is only a link to a music service (Spotify, Apple Music, YouTube Music, SoundCloud, Яндекс Музыка,
  Deezer, Tidal, Bandcamp, VK, Amazon Music) starts the same download. A bare YouTube link is ambiguous (a video to
  discuss or a song), so the bot asks with two buttons: «В MemuMusic» / «Спросить ИИ».

The downloads run through ``app.music.tgapp_ops.call`` (the MemuAI window when it runs, else in-process here).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Dict, Optional

log = logging.getLogger("memuai.music.telegram")

COMMANDS = ("/music", "/song", "/музыка")
_URL = r"https?://[^\s<>\"']{4,2000}"
MUSIC_HOSTS = re.compile(
    r"^https?://(?:[\w-]+\.)*(?:open\.spotify\.com|spotify\.link|music\.apple\.com|itunes\.apple\.com|music\.youtube\.com|"
    r"soundcloud\.com|on\.soundcloud\.com|music\.yandex\.(?:ru|com|by|kz|uz)|deezer\.com|deezer\.page\.link|link\.deezer\.com|"
    r"tidal\.com|listen\.tidal\.com|bandcamp\.com|vk\.com/(?:music|audio)|music\.vk\.com|music\.amazon\.[\w.]+)(?:[/?#]|$)", re.I)
YOUTUBE = re.compile(r"^https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch|shorts/|playlist)|youtu\.be/)", re.I)
POLL_SECONDS = 2.5
EDIT_EVERY = 3.0
MAX_WATCH = 45 * 60
_pending: Dict[str, Dict[str, Any]] = {}       # chat → {url, at}: a bare YouTube link waiting for a button
_watching: set = set()                          # job ids followed by a progress message

SOURCE_NAMES = {"youtube": "YouTube", "ytmusic": "YouTube Music", "spotify": "Spotify", "apple": "Apple Music",
                "soundcloud": "SoundCloud", "deezer": "Deezer", "yandex": ("Яндекс Музыка", "Yandex Music"), "vk": "VK",
                "bandcamp": "Bandcamp", "tidal": "Tidal", "amazon": "Amazon Music"}
KINDS = {"track": ("трек", "track"), "album": ("альбом", "album"), "playlist": ("плейлист", "playlist"),
         "artist": ("исполнитель", "artist")}


def _t(lang: str, ru: str, en: str) -> str:
    return en if lang == "en" else ru


def bare_link(text: str) -> str:
    """The URL when the message is only a link (spaces / one trailing word like «скачай» are not allowed — exact)."""
    text = str(text or "").strip()
    return text if re.fullmatch(_URL, text) else ""


def classify(text: str) -> str:
    """``music`` — a bare link to a music service; ``youtube`` — a bare YouTube link (ask); '' — anything else."""
    url = bare_link(text)
    if not url:
        return ""
    if MUSIC_HOSTS.match(url):
        return "music"
    if YOUTUBE.match(url):
        return "youtube"
    return ""


def source_name(source: str, lang: str) -> str:
    name = SOURCE_NAMES.get(source or "", "")
    if isinstance(name, tuple):
        return name[1] if lang == "en" else name[0]
    return name or (source or "").capitalize() or _t(lang, "сайт", "website")


def _bar(progress: float, width: int = 12) -> str:
    filled = max(0, min(width, int(round(progress * width))))
    return "▰" * filled + "▱" * (width - filled)


def _tracks(n: int, lang: str) -> str:
    if lang == "en":
        return f"{n} track" + ("" if n == 1 else "s")
    a, b = abs(n) % 100, abs(n) % 10
    word = "треков" if 10 < a < 20 else "трек" if b == 1 else "трека" if 2 <= b <= 4 else "треков"
    return f"{n} {word}"


def progress_text(job: Dict[str, Any], lang: str) -> str:
    """One bot message for a download job (plain text)."""
    source = source_name(str(job.get("source") or ""), lang)
    kind = KINDS.get(str(job.get("item_kind") or ""), None)
    head = f"{source} · {kind[1] if lang == 'en' else kind[0]}" if kind else source
    title = str(job.get("title") or job.get("url") or "")[:200]
    status = str(job.get("status") or "queued")
    total, done, failed = int(job.get("total") or 0), int(job.get("done") or 0), int(job.get("failed") or 0)
    if status == "done":
        got = len(job.get("track_ids") or []) or done
        line = _t(lang, f"✅ Скачано в MemuMusic — {_tracks(got, lang)}", f"✅ Downloaded to MemuMusic — {_tracks(got, lang)}")
        if failed:
            line += _t(lang, f", не удалось: {failed}", f", failed: {failed}")
        return f"{line}\n{head}\n«{title}»" if lang != "en" else f"{line}\n{head}\n“{title}”"
    if status in ("error", "cancelled"):
        reason = str(job.get("error") or "")[:300]
        first = _t(lang, "⛔ Загрузка отменена", "⛔ Download cancelled") if status == "cancelled" else \
            _t(lang, "⚠️ Не удалось скачать", "⚠️ Download failed")
        return f"{first}\n{head}\n{title}" + (f"\n{reason}" if reason and status == "error" else "")
    progress = float(job.get("progress") or 0)
    count = f" · {done}/{total}" if total > 1 else ""
    state = _t(lang, "в очереди", "queued") if status == "queued" else f"{int(progress * 100)}%{count}"
    current = str(job.get("current") or "")[:120] if isinstance(job.get("current"), str) else ""
    return (_t(lang, "⏬ Компьютер скачивает в MemuMusic", "⏬ The computer is downloading to MemuMusic") +
            f"\n{head}\n{title}\n{_bar(progress)} {state}" + (f"\n♪ {current}" if current and total > 1 else ""))


def music_tab_url(webapp_url: Optional[str]) -> str:
    if not webapp_url:
        return ""
    return webapp_url + ("&" if "?" in webapp_url else "?") + "tab=music"


def _keyboard(job: Dict[str, Any], lang: str, webapp_url: Optional[str]) -> Optional[Dict[str, Any]]:
    status = str(job.get("status") or "")
    if status in ("queued", "running"):
        return {"inline_keyboard": [[{"text": _t(lang, "✖ Отменить", "✖ Cancel"),
                                      "callback_data": f"mus:x:{str(job.get('job_id') or '')[:48]}"}]]}
    url = music_tab_url(webapp_url)
    if status == "done" and url:
        return {"inline_keyboard": [[{"text": _t(lang, "🎵 Открыть музыку", "🎵 Open music"), "web_app": {"url": url}}]]}
    return None


async def start(client: Any, token: str, chat_id: str, prefs: Dict[str, Any], url: str, *, reply_to: int = 0) -> None:
    """Start the download on the computer and follow it in one edited message."""
    from .. import telegram_bot as tb
    from . import tgapp_ops
    lang = tb._lang(prefs)
    try:
        result = await tgapp_ops.call("download.start", {"url": url, "quality": "best"}, lang=lang) or {}
    except tgapp_ops.MusicUnavailable:
        await tb._send(client, token, chat_id, _t(lang, "MemuMusic недоступен на компьютере — обновите MemuAI.",
                                                  "MemuMusic is not available on the computer — update MemuAI."), reply_to=reply_to)
        return
    except Exception as exc:
        await tb._send(client, token, chat_id, _t(lang, "⚠️ Не удалось начать загрузку: ", "⚠️ Could not start the download: ")
                       + str(exc)[:300], reply_to=reply_to)
        return
    job_id = str(result.get("job_id") or "")
    job = {"job_id": job_id, "url": url, "source": result.get("source"), "item_kind": result.get("kind"),
           "status": "queued", "title": url}
    webapp = tb._webapp_url()
    sent = await tb._send(client, token, chat_id, progress_text(job, lang), _keyboard(job, lang, webapp),
                          reply_to=reply_to, silent=True)
    if not job_id or not sent:
        return
    if job_id in _watching:
        return                                   # the same link is already followed in another message
    _watching.add(job_id)
    try:
        await follow(client, token, chat_id, sent[-1], job_id, lang, webapp)
    finally:
        _watching.discard(job_id)


async def follow(client: Any, token: str, chat_id: str, message_id: int, job_id: str, lang: str,
                 webapp: Optional[str], *, poll: float = POLL_SECONDS, max_watch: float = MAX_WATCH) -> Dict[str, Any]:
    """Edit the progress message until the job ends (or ``max_watch`` passes). Returns the last job seen."""
    from .. import telegram_bot as tb
    from . import tgapp_ops
    last_text, last_edit, began, job = "", 0.0, time.monotonic(), {}
    while time.monotonic() - began < max_watch:
        await asyncio.sleep(poll)
        try:
            rows = await tgapp_ops.call("download.jobs", {}, lang=lang) or []
        except Exception:
            continue
        rows = rows.get("jobs", []) if isinstance(rows, dict) else rows
        job = next((j for j in rows if isinstance(j, dict) and str(j.get("job_id")) == job_id), None) or {}
        if not job:
            break
        finished = job.get("status") in ("done", "error", "cancelled")
        text = progress_text(job, lang)
        if text != last_text and (finished or time.monotonic() - last_edit >= EDIT_EVERY):
            payload: Dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text,
                                       "link_preview_options": {"is_disabled": True}}
            markup = _keyboard(job, lang, webapp)
            payload["reply_markup"] = markup or {"inline_keyboard": []}
            try:
                await tb._call(client, token, "editMessageText", **payload)
            except Exception as exc:
                if "not modified" not in str(exc):
                    log.info("telegram music progress: %s", tb._redact_token(str(exc))[:200])
            last_text, last_edit = text, time.monotonic()
        if finished:
            break
    return job


def ask_markup(lang: str) -> Dict[str, Any]:
    return {"inline_keyboard": [[{"text": _t(lang, "⬇️ В MemuMusic", "⬇️ To MemuMusic"), "callback_data": "mus:dl"},
                                 {"text": _t(lang, "💬 Спросить ИИ", "💬 Ask the AI"), "callback_data": "mus:ai"}]]}


async def handle_text(client: Any, token: str, chat_id: str, prefs: Dict[str, Any], text: str, *, reply_to: int = 0) -> bool:
    """A bare music link → download; a bare YouTube link → ask. True when the message was handled here."""
    from .. import telegram_bot as tb
    if prefs.get("telegram_music_links", True) is False:
        return False
    kind = classify(text)
    if not kind:
        return False
    url = bare_link(text)
    if kind == "music":
        tb._spawn_answer(start(client, token, chat_id, prefs, url, reply_to=reply_to))
        return True
    lang = tb._lang(prefs)
    _pending[chat_id] = {"url": url, "at": time.time(), "reply_to": reply_to}
    await tb._send(client, token, chat_id, _t(lang, "Скачать это в MemuMusic на компьютере или спросить ИИ про видео?",
                                              "Download this to MemuMusic on the computer or ask the AI about the video?"),
                   ask_markup(lang), reply_to=reply_to, silent=True)
    return True


async def command(client: Any, token: str, chat_id: str, prefs: Dict[str, Any], argument: str, *, reply_to: int = 0) -> None:
    """``/music <ссылка>`` (or a search text: the computer finds it on YouTube Music); without an argument — help."""
    from .. import telegram_bot as tb
    lang = tb._lang(prefs)
    argument = str(argument or "").strip()
    if not argument:
        url = music_tab_url(tb._webapp_url())
        await tb._send(client, token, chat_id, _t(
            lang, "🎵 MemuMusic: пришлите ссылку на трек, альбом или плейлист — компьютер скачает его в библиотеку.\n"
                  "Например: /music https://open.spotify.com/track/…\n"
                  "Подходят Spotify, YouTube, YouTube Music, Apple Music, SoundCloud, Яндекс Музыка, VK, Deezer… "
                  "Можно и просто отправить ссылку сообщением.",
            "🎵 MemuMusic: send a link to a track, album or playlist — the computer downloads it into the library.\n"
            "For example: /music https://open.spotify.com/track/…\n"
            "Spotify, YouTube, YouTube Music, Apple Music, SoundCloud, Deezer, Yandex Music, VK… "
            "You can also just send the link as a message."),
            {"inline_keyboard": [[{"text": _t(lang, "🎵 Открыть музыку", "🎵 Open music"), "web_app": {"url": url}}]]} if url else None,
            reply_to=reply_to)
        return
    tb._spawn_answer(start(client, token, chat_id, prefs, argument[:2000], reply_to=reply_to))


async def handle_callback(client: Any, token: str, chat_id: str, message_id: int, data: str, prefs: Dict[str, Any]) -> str:
    """``mus:x:<job>`` cancel · ``mus:dl`` / ``mus:ai`` — the answer to a bare YouTube link. Returns the toast."""
    from .. import telegram_bot as tb
    from . import tgapp_ops
    lang = tb._lang(prefs)
    if data.startswith("mus:x:"):
        try:
            await tgapp_ops.call("download.cancel", {"job_id": data[6:]}, lang=lang)
        except Exception as exc:
            return str(exc)[:180]
        return _t(lang, "Отменяю…", "Cancelling…")
    pending = _pending.pop(chat_id, None)
    if not pending or time.time() - pending["at"] > 3600:
        return _t(lang, "Ссылка устарела — пришлите её снова", "The link is too old — send it again")
    await tb._delete_quietly(client, token, chat_id, message_id)
    if data == "mus:dl":
        tb._spawn_answer(start(client, token, chat_id, prefs, pending["url"], reply_to=int(pending.get("reply_to") or 0)))
        return _t(lang, "Скачиваю на компьютер", "Downloading on the computer")
    tb._spawn_answer(tb._answer_ai(client, token, chat_id, pending["url"], prefs, reply_to=int(pending.get("reply_to") or 0)))
    return ""
