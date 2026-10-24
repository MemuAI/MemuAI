"""Анализ видео YouTube без API-ключа (Round 24, r24-quick-settings).

Цепочка получения данных:
  1. ``video_id(url)`` — watch / youtu.be / shorts / embed / live / m.youtube / music.youtube, голый id.
  2. Метаданные — oEmbed (``https://www.youtube.com/oembed?url=…&format=json``): название, автор, превью.
  3. Субтитры — плеер InnerTube (``youtubei/v1/player``, клиент ANDROID) → ``captions…captionTracks`` → выбор
     дорожки (язык пользователя: ручные → авто-субтитры ``kind=asr``; иначе — оригинальная ручная/любая: модель
     всё равно отвечает на языке пользователя, а машинный перевод YouTube ``tlang`` хуже оригинала)
     → ``timedtext`` в формате json3 (fallback — vtt). Ссылки ``timedtext`` со страницы ``watch``
     (``ytInitialPlayerResponse``) с 2025 г. несут ``exp=xpe`` и без PO-токена отдают пустое тело (Round 25),
     поэтому страница ``watch`` — запасной путь (и источник статуса «нужен вход»/«недоступно»).
  4. Fallback — ``yt-dlp`` (если установлен: модуль или программа в PATH) — только чтение субтитров.
  5. Иначе — честная ошибка ``TranscriptUnavailable`` с причиной (нет субтитров, вход/возраст, сеть).

Дальше: ``chunk_segments`` режет транскрипт на куски по ~``chunk_chars`` с таймкодами, ``analyze`` суммирует
моделью (map → reduce для длинных видео) в режимах ``brief`` / ``detailed`` / ``timestamps`` / ``question``.
Таймкоды в ответе модели (``[12:34]``) превращаются в ссылки ``…&t=754s`` (``link_timestamps``).
Кэш — в памяти процесса (LRU, по id видео + язык), чтобы вопросы по тому же видео не скачивали его заново.

Сетевые функции принимают ``fetch`` (async ``(url) -> str``) и ``post`` (async ``(url, json) -> str``) — тесты
подставляют фикстуры без сети. Свой ``fetch`` без ``post`` отключает шаг InnerTube.
"""
from __future__ import annotations

import asyncio
import html
import json
import re
import shutil
import subprocess
import sys
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, urlencode, urlparse

try:  # аннотации — строки (from __future__), FastAPI ищет Request в глобальных именах модуля
    from fastapi import Request
except Exception:  # pragma: no cover
    Request = Any  # type: ignore

Fetch = Callable[[str], Awaitable[str]]
Post = Callable[[str, Dict[str, Any]], Awaitable[str]]
Complete = Callable[[str], Awaitable[str]]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
MODES = ("brief", "detailed", "timestamps", "question")
CACHE_SIZE = 24
INNERTUBE_PLAYER = "https://www.youtube.com/youtubei/v1/player"
INNERTUBE_CLIENT = {"clientName": "ANDROID", "clientVersion": "20.10.38"}
MAP_CONCURRENCY = 4


class TranscriptUnavailable(RuntimeError):
    def __init__(self, message: str, code: str = "no_captions", en: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.en = en or message

    def text(self, lang: str) -> str:
        """Round 26: the card of an English UI showed the Russian reason."""
        return self.en if lang == "en" else str(self)


# ------------------------------------------------------------------ ссылки
def video_id(value: Any) -> str:
    """Id видео из ссылки/текста или ``""``."""
    text = str(value or "").strip()
    if _ID.match(text):
        return text
    m = re.search(r"(https?://)?(www\.|m\.|music\.)?(youtube\.com|youtu\.be|youtube-nocookie\.com)/[^\s<>\"']+", text)
    if not m:
        return ""
    raw = m.group(0)
    if not raw.startswith("http"):
        raw = "https://" + raw
    u = urlparse(raw)
    host = u.netloc.lower()
    if host.endswith("youtu.be"):
        cand = u.path.strip("/").split("/")[0]
        return cand if _ID.match(cand) else ""
    q = parse_qs(u.query)
    if q.get("v") and _ID.match(q["v"][0]):
        return q["v"][0]
    parts = [p for p in u.path.split("/") if p]
    if len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v", "e"):
        return parts[1] if _ID.match(parts[1]) else ""
    return ""


def find_url(text: Any) -> str:
    m = re.search(r"https?://(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)/[^\s<>\"']+", str(text or ""))
    return m.group(0) if m else ""


def watch_url(vid: str, seconds: Optional[int] = None) -> str:
    base = "https://www.youtube.com/watch?v=" + vid
    return base + (f"&t={int(seconds)}s" if seconds else "")


def fmt_ts(seconds: float) -> str:
    s = int(max(0, seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def parse_ts(text: str) -> Optional[int]:
    parts = str(text).strip().split(":")
    if not 2 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        return None
    vals = [int(p) for p in parts]
    if len(vals) == 2:
        return vals[0] * 60 + vals[1] if vals[1] < 60 else None
    return vals[0] * 3600 + vals[1] * 60 + vals[2] if vals[1] < 60 and vals[2] < 60 else None


def link_timestamps(text: str, vid: str) -> str:
    """``[12:34]`` / ``(1:02:03)`` → markdown-ссылка ``[12:34](https://www.youtube.com/watch?v=…&t=754s)``."""
    def repl(m: "re.Match[str]") -> str:
        sec = parse_ts(m.group(2))
        if sec is None:
            return m.group(0)
        return f"[{m.group(2)}]({watch_url(vid, sec)})"
    return re.sub(r"([\[(])(\d{1,2}:\d{2}(?::\d{2})?)[\])](?!\()", repl, text)


# ------------------------------------------------------------------ сеть
async def http_get(url: str) -> str:
    import httpx
    async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=10), follow_redirects=True,
                                 headers={"User-Agent": UA, "Accept-Language": "ru,en;q=0.8"},
                                 cookies={"CONSENT": "YES+cb"}) as client:
        r = await client.get(url)
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}")
        return r.text


async def http_post(url: str, payload: Dict[str, Any]) -> str:
    import httpx
    async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=10), follow_redirects=True,
                                 headers={"Accept-Language": "ru,en;q=0.8"}) as client:
        r = await client.post(url, json=payload)
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}")
        return r.text


async def innertube_player(vid: str, lang: str, post: Post) -> Dict[str, Any]:
    """Ответ плеера InnerTube (клиент ANDROID): его ссылки ``timedtext`` работают без PO-токена."""
    data = json.loads(await post(INNERTUBE_PLAYER, {"context": {"client": {**INNERTUBE_CLIENT, "hl": lang}},
                                                    "videoId": vid}))
    return data if isinstance(data, dict) else {}


async def oembed(vid: str, fetch: Fetch = http_get) -> Dict[str, Any]:
    url = "https://www.youtube.com/oembed?" + urlencode({"url": watch_url(vid), "format": "json"})
    try:
        data = json.loads(await fetch(url))
    except Exception:
        return {"id": vid, "url": watch_url(vid)}
    return {"id": vid, "url": watch_url(vid), "title": str(data.get("title") or ""),
            "author": str(data.get("author_name") or ""), "author_url": str(data.get("author_url") or ""),
            "thumbnail": str(data.get("thumbnail_url") or "")}


def player_response(page: str) -> Dict[str, Any]:
    """``ytInitialPlayerResponse`` из HTML страницы watch (сбалансированный разбор JSON-объекта)."""
    m = re.search(r"ytInitialPlayerResponse\s*=\s*\{", page)
    if not m:
        return {}
    start = m.end() - 1
    depth, i, in_str, escp = 0, start, False, False
    while i < len(page):
        ch = page[i]
        if in_str:
            if escp:
                escp = False
            elif ch == "\\":
                escp = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(page[start:i + 1])
                except Exception:
                    return {}
        i += 1
    return {}


def caption_tracks(player: Dict[str, Any]) -> List[Dict[str, Any]]:
    tracks = (((player.get("captions") or {}).get("playerCaptionsTracklistRenderer") or {}).get("captionTracks") or [])
    out = []
    for t in tracks:
        if not isinstance(t, dict) or not t.get("baseUrl"):
            continue
        name = t.get("name") or {}
        label = name.get("simpleText") or "".join(r.get("text", "") for r in name.get("runs", []) if isinstance(r, dict))
        out.append({"url": t["baseUrl"], "lang": str(t.get("languageCode") or ""), "auto": t.get("kind") == "asr",
                    "name": label, "translatable": bool(t.get("isTranslatable"))})
    return out


def pick_track(tracks: List[Dict[str, Any]], lang: str = "ru") -> Tuple[Optional[Dict[str, Any]], str]:
    """(дорожка, tlang): язык пользователя (ручная → авто) → любая ручная → первая. ``tlang`` зарезервирован
    (пусто): оригинальные субтитры точнее машинного перевода YouTube."""
    if not tracks:
        return None, ""
    def first(pred):
        return next((t for t in tracks if pred(t)), None)
    for pred in (lambda t: t["lang"].split("-")[0] == lang and not t["auto"],
                 lambda t: t["lang"].split("-")[0] == lang):
        hit = first(pred)
        if hit:
            return hit, ""
    best = first(lambda t: not t["auto"]) or tracks[0]
    return best, ""


def parse_json3(text: str) -> List[Dict[str, Any]]:
    if not text.strip():
        return []
    data = json.loads(text)
    segs = []
    for ev in data.get("events") or []:
        parts = ev.get("segs")
        if not parts:
            continue
        line = "".join(str(p.get("utf8", "")) for p in parts).replace("\n", " ").strip()
        if line:
            segs.append({"start": float(ev.get("tStartMs", 0)) / 1000.0, "text": html.unescape(line)})
    return segs


def parse_vtt(text: str) -> List[Dict[str, Any]]:
    segs: List[Dict[str, Any]] = []
    cur: Optional[float] = None
    buf: List[str] = []
    def flush():
        if cur is not None and buf:
            line = re.sub(r"<[^>]+>", "", " ".join(buf)).strip()
            if line and not (segs and segs[-1]["text"] == line):
                segs.append({"start": cur, "text": html.unescape(line)})
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r"(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})\s+-->", line)
        if m:
            flush(); buf = []
            h = int(m.group(1) or 0)
            cur = h * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000
        elif line and not line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")) and cur is not None:
            buf.append(line)
        elif not line:
            flush(); buf = []
    flush()
    return segs


def _merge_rolling(segs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Авто-субтитры повторяют хвост предыдущей строки — убираем повторы."""
    out: List[Dict[str, Any]] = []
    for s in segs:
        if out and s["text"].startswith(out[-1]["text"]) and len(s["text"]) > len(out[-1]["text"]):
            out[-1] = {"start": out[-1]["start"], "text": s["text"]}
        elif not out or s["text"] != out[-1]["text"]:
            out.append(s)
    return out


async def _ytdlp(vid: str, lang: str) -> List[Dict[str, Any]]:
    """Fallback через yt-dlp (если установлен); только субтитры, без скачивания видео."""
    exe = shutil.which("yt-dlp")
    cmd: Optional[List[str]] = [exe] if exe else None
    if cmd is None:
        if getattr(sys, "frozen", False):  # sys.executable — сам MemuAI.exe, «-m yt_dlp» запустил бы программу заново
            return []
        try:
            import yt_dlp  # noqa: F401
            cmd = [sys.executable, "-m", "yt_dlp"]
        except Exception:
            return []
    import tempfile, os
    with tempfile.TemporaryDirectory() as tmp:
        args = cmd + ["--skip-download", "--write-subs", "--write-auto-subs", "--sub-format", "vtt",
                      "--sub-langs", f"{lang}.*,{lang},en.*,en", "-o", os.path.join(tmp, "%(id)s"), watch_url(vid)]
        def run():
            return subprocess.run(args, capture_output=True, timeout=90,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            await asyncio.to_thread(run)
        except Exception:
            return []
        files = sorted(f for f in os.listdir(tmp) if f.endswith(".vtt"))
        files.sort(key=lambda f: (0 if f".{lang}" in f else 1, f))
        for f in files:
            with open(os.path.join(tmp, f), encoding="utf-8", errors="replace") as fh:
                segs = _merge_rolling(parse_vtt(fh.read()))
            if segs:
                return segs
    return []


def _status(player: Dict[str, Any]) -> str:
    return str((player.get("playabilityStatus") or {}).get("status") or "")


def _duration(player: Dict[str, Any]) -> int:
    try:
        return int((player.get("videoDetails") or {}).get("lengthSeconds") or 0)
    except (TypeError, ValueError):
        return 0


async def _track_segments(tracks: List[Dict[str, Any]], lang: str, fetch: Fetch) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], str]:
    """(дорожка, сегменты, причина неудачи) — json3, затем vtt."""
    track, tlang = pick_track(tracks, lang)
    if not track:
        return None, [], ""
    reason = ""
    base = re.sub(r"&fmt=[^&]*", "", track["url"])
    for fmt in ("json3", "vtt"):
        url = base + ("&" if "?" in base else "?") + "fmt=" + fmt + (f"&tlang={quote(tlang)}" if tlang else "")
        try:
            body = await fetch(url)
        except Exception as exc:
            reason = f"timedtext: {exc}"
            continue
        if not body.strip():
            reason = "YouTube вернул пустые субтитры"
            continue
        try:
            segs = _merge_rolling(parse_json3(body) if fmt == "json3" else parse_vtt(body))
        except Exception as exc:
            reason = f"timedtext: {type(exc).__name__}"
            continue
        if segs:
            return {**track, "lang": tlang or track["lang"]}, segs, ""
    return track, [], reason


async def transcript(vid: str, lang: str = "ru", fetch: Fetch = http_get, use_ytdlp: bool = True,
                     post: Optional[Post] = None) -> Dict[str, Any]:
    """``{segments, lang, auto, source, tracks, duration}`` или ``TranscriptUnavailable``."""
    if post is None and fetch is http_get:
        post = http_post
    reasons: List[str] = []
    players: List[Dict[str, Any]] = []

    def result(player: Dict[str, Any], tracks: List[Dict[str, Any]], track: Dict[str, Any],
               segs: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {"segments": segs, "lang": track["lang"], "auto": track["auto"], "source": "timedtext",
                "tracks": [{k: t[k] for k in ("lang", "auto", "name")} for t in tracks], "duration": _duration(player)}

    if post is not None:
        try:
            player = await innertube_player(vid, lang, post)
        except Exception as exc:
            player = {}
            reasons.append(f"InnerTube: {str(exc)[:120] or type(exc).__name__}")
        if player:
            players.append(player)
            tracks = caption_tracks(player)
            track, segs, reason = await _track_segments(tracks, lang, fetch)
            if track and segs:
                return result(player, tracks, track, segs)
            if reason:
                reasons.append(reason)
    page = ""
    try:
        page = await fetch(watch_url(vid) + "&hl=" + lang)
    except Exception as exc:
        reasons.append(f"сеть: {str(exc)[:120] or type(exc).__name__}")
    player = player_response(page) if page else {}
    if player:
        players.insert(0, player)          # статус страницы watch точнее статуса клиента ANDROID
    tracks = caption_tracks(player)
    track, segs, reason = await _track_segments(tracks, lang, fetch)
    if track and segs:
        return result(player, tracks, track, segs)
    if reason:
        reasons.append(reason)
    duration = max((_duration(p) for p in players), default=0)
    if use_ytdlp:
        segs = await _ytdlp(vid, lang)
        if segs:
            return {"segments": segs, "lang": lang, "auto": True, "source": "yt-dlp", "tracks": [], "duration": duration}
    status = _status(players[0]) if players else ""
    # Round 34: YouTube answers a bot check with LOGIN_REQUIRED too («Sign in to confirm you're not a bot») — the
    # video is fine and needs no account; the requests from this address are rate-limited. «Needs a sign-in or age
    # confirmation» sent people to look for an account problem that does not exist.
    why = " ".join(str((p.get("playabilityStatus") or {}).get(k) or "") for p in players for k in ("reason", "messages"))
    if status == "LOGIN_REQUIRED" and re.search(r"(?i)not a bot|не робот|unusual traffic|bot", why):
        raise TranscriptUnavailable("YouTube принял запросы MemuAI за бота и временно не отдаёт субтитры (видео в порядке, "
                                    "вход не нужен). Попробуйте через несколько минут, смените сеть/VPN или установите yt-dlp.",
                                    "bot_check",
                                    "YouTube took MemuAI's requests for a bot and does not give the captions for now (the video "
                                    "is fine, no sign-in is needed). Try again in a few minutes, switch the network/VPN or "
                                    "install yt-dlp.")
    if status in ("LOGIN_REQUIRED", "AGE_VERIFICATION_REQUIRED"):
        raise TranscriptUnavailable("Видео требует входа или подтверждения возраста — субтитры недоступны без аккаунта.", "login",
                                    "The video needs a sign-in or age confirmation — captions are not available without an account.")
    if status in ("ERROR", "UNPLAYABLE"):
        raise TranscriptUnavailable("Видео недоступно (удалено, приватное или закрыто в регионе).", "unavailable",
                                    "The video is unavailable (removed, private or blocked in the region).")
    if players and not any(caption_tracks(p) for p in players):
        raise TranscriptUnavailable("У этого видео нет субтитров (ни ручных, ни автоматических). "
                                    "Установите yt-dlp или откройте видео и задайте вопрос по описанию.", "no_captions",
                                    "This video has no captions (neither manual nor automatic). Install yt-dlp or ask about "
                                    "the video description instead.")
    reason = "; ".join(dict.fromkeys(reasons))
    raise TranscriptUnavailable("Не удалось получить субтитры" + (f" ({reason})" if reason else "") + ".", "fetch",
                                "Could not get the captions" + (f" ({reason})" if reason else "") + ".")


# ------------------------------------------------------------------ чанки и анализ
def chunk_segments(segs: List[Dict[str, Any]], chunk_chars: int = 9000) -> List[str]:
    """Текст с метками ``[m:ss]`` каждые ~30 с, нарезанный на куски не длиннее ``chunk_chars``."""
    chunks: List[str] = []
    buf: List[str] = []
    size = 0
    last_mark = -999.0
    for s in segs:
        piece = s["text"]
        if s["start"] - last_mark >= 30:
            piece = f"[{fmt_ts(s['start'])}] " + piece
            last_mark = s["start"]
        if size + len(piece) + 1 > chunk_chars and buf:
            chunks.append(" ".join(buf)); buf, size = [], 0
            if not piece.startswith("["):
                piece = f"[{fmt_ts(s['start'])}] " + piece
                last_mark = s["start"]
        buf.append(piece); size += len(piece) + 1
    if buf:
        chunks.append(" ".join(buf))
    return chunks


def _instruction(mode: str, lang: str, question: str = "") -> str:
    ru = lang != "en"
    if mode == "detailed":
        return ("Сделай подробный конспект видео: основные разделы с подзаголовками, ключевые мысли, факты, цифры и выводы. "
                "Указывай таймкоды в формате [м:сс]." if ru else
                "Write a detailed outline of the video: sections with headings, key ideas, facts, numbers, conclusions. "
                "Cite timestamps as [m:ss].")
    if mode == "timestamps":
        return ("Составь оглавление видео: 6–15 пунктов вида «[м:сс] — о чём этот момент». Только список." if ru else
                "Make a table of contents: 6–15 lines like “[m:ss] — what happens”. List only.")
    if mode == "question":
        return ((f"Ответь на вопрос по видео: «{question}». Опирайся только на транскрипт, укажи таймкоды [м:сс] "
                 "подтверждающих мест; если ответа в видео нет — так и скажи.") if ru else
                (f"Answer the question about the video: “{question}”. Use only the transcript, cite [m:ss] timestamps; "
                 "if the video does not say, say so."))
    return ("Кратко перескажи видео: 3–6 пунктов с главным и одна строка вывода. Добавь 2–3 ключевых таймкода [м:сс]." if ru else
            "Summarize the video briefly: 3–6 bullet points and a one-line takeaway. Add 2–3 key [m:ss] timestamps.")


async def summarize(meta: Dict[str, Any], segs: List[Dict[str, Any]], complete: Complete, *, mode: str = "brief",
                    lang: str = "ru", question: str = "", chunk_chars: int = 9000, max_chunks: int = 12) -> str:
    mode = mode if mode in MODES else "brief"
    ru = lang != "en"
    total = sum(len(s["text"]) + 1 for s in segs)
    # Длинное видео: сначала укрупняем куски (до 3×), чтобы модель видела весь транскрипт; выборка кусков —
    # только для очень длинных записей, где иначе не хватит контекста модели.
    chunks = chunk_segments(segs, min(max(chunk_chars, -(-total // max_chunks)), chunk_chars * 3))
    head = (f"Видео: «{meta.get('title') or meta.get('id')}» — {meta.get('author') or ''}\n" if ru else
            f"Video: “{meta.get('title') or meta.get('id')}” by {meta.get('author') or ''}\n")
    task = _instruction(mode, lang, question)
    if len(chunks) > max_chunks:          # очень длинное видео: равномерная выборка кусков
        step = len(chunks) / max_chunks
        chunks = [chunks[int(i * step)] for i in range(max_chunks)]
    if len(chunks) == 1:
        return await complete(f"{head}{task}\n\n<transcript>\n{chunks[0]}\n</transcript>")
    gate = asyncio.Semaphore(MAP_CONCURRENCY)

    async def note(i: int, ch: str) -> str:   # map: заметки по каждому куску, с таймкодами
        if ru:
            ask = (f"Часть {i}/{len(chunks)} транскрипта. Выпиши главное этой части кратко, сохраняя таймкоды [м:сс]"
                   + (f" и всё, что относится к вопросу «{question}»" if mode == "question" else "") + ".")
        else:
            ask = (f"Part {i}/{len(chunks)} of the transcript. Note the key points of this part briefly, keeping the [m:ss] "
                   "timestamps" + (f" and everything related to the question “{question}”" if mode == "question" else "") + ".")
        async with gate:
            return await complete(f"{head}{ask}\n\n<transcript>\n{ch}\n</transcript>")
    notes = await asyncio.gather(*(note(i, ch) for i, ch in enumerate(chunks, 1)))
    part = "Часть" if ru else "Part"
    joined = "\n\n".join(f"## {part} {i}\n{n}" for i, n in enumerate(notes, 1))
    tail = "Ниже — заметки по частям видео по порядку." if ru else "Below are notes on the parts of the video, in order."
    return await complete(f"{head}{task}\n{tail}\n\n{joined}")


_CACHE: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()


def _cache_get(key: str) -> Optional[Dict[str, Any]]:
    hit = _CACHE.get(key)
    if hit and time.time() - hit["ts"] < 6 * 3600:
        _CACHE.move_to_end(key)
        return hit
    return None


def _cache_put(key: str, value: Dict[str, Any]) -> None:
    value["ts"] = time.time()
    _CACHE[key] = value
    _CACHE.move_to_end(key)
    while len(_CACHE) > CACHE_SIZE:
        _CACHE.popitem(last=False)


async def load(url: str, lang: str = "ru", fetch: Fetch = http_get, use_ytdlp: bool = True,
               post: Optional[Post] = None) -> Dict[str, Any]:
    vid = video_id(url)
    if not vid:
        raise TranscriptUnavailable("Это не похоже на ссылку на видео YouTube.", "bad_url", "This is not a YouTube video link.")
    key = f"{vid}:{lang}"
    hit = _cache_get(key)
    if hit:
        return hit
    meta = await oembed(vid, fetch)
    tr = await transcript(vid, lang, fetch, use_ytdlp, post)
    data = {"meta": meta, **tr}
    _cache_put(key, data)
    return data


async def analyze(url: str, complete: Complete, *, mode: str = "brief", lang: str = "ru", question: str = "",
                  fetch: Fetch = http_get, use_ytdlp: bool = True, post: Optional[Post] = None) -> Dict[str, Any]:
    data = await load(url, lang, fetch, use_ytdlp, post)
    meta = data["meta"]
    mode = mode if mode in MODES else "brief"
    akey = f"{mode}:{question.strip().lower()}"
    cached = data.setdefault("answers", {}).get(akey)
    if cached:
        text = cached
    else:
        text = await summarize(meta, data["segments"], complete, mode=mode, lang=lang, question=question)
        data["answers"][akey] = text
    words = sum(len(s["text"].split()) for s in data["segments"])
    return {"id": meta["id"], "title": meta.get("title", ""), "author": meta.get("author", ""), "url": meta["url"],
            "thumbnail": meta.get("thumbnail", ""), "mode": mode, "text": link_timestamps(text, meta["id"]),
            "transcript": {"lang": data.get("lang"), "auto": data.get("auto"), "source": data.get("source"),
                           "segments": len(data["segments"]), "words": words, "duration": data.get("duration", 0)}}


def register(app, complete_factory: Callable[[], Optional[Complete]], fetch: Optional[Fetch] = None,
             use_ytdlp: bool = True, post: Optional[Post] = None) -> None:
    """``POST /api/youtube/analyze {url, mode, question, lang}`` и ``POST /api/youtube/transcript {url, lang}``."""
    from fastapi.responses import JSONResponse

    @app.post("/api/youtube/analyze")
    async def youtube_analyze(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        body = body if isinstance(body, dict) else {}
        lang = "en" if body.get("lang") == "en" else "ru"
        if not video_id(body.get("url")):
            return JSONResponse({"error": "This is not a YouTube video link." if lang == "en" else
                                 "Это не похоже на ссылку на видео YouTube.", "code": "bad_url"}, 422)
        complete = complete_factory()
        if complete is None:
            return JSONResponse({"error": "No model available" if lang == "en" else "Модель недоступна", "code": "no_model"}, 503)
        try:
            return await analyze(str(body.get("url") or ""), complete, mode=str(body.get("mode") or "brief"), lang=lang,
                                 question=str(body.get("question") or "")[:500], fetch=fetch or http_get, use_ytdlp=use_ytdlp,
                                 post=post)
        except TranscriptUnavailable as exc:
            return JSONResponse({"error": exc.text(lang), "code": exc.code}, 422)
        except Exception as exc:
            return JSONResponse({"error": str(exc)[:400], "code": "failed"}, 502)

    @app.post("/api/youtube/transcript")
    async def youtube_transcript(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        body = body if isinstance(body, dict) else {}
        try:
            data = await load(str(body.get("url") or ""), "en" if body.get("lang") == "en" else "ru", fetch or http_get,
                              use_ytdlp, post)
        except TranscriptUnavailable as exc:
            return JSONResponse({"error": str(exc), "code": exc.code}, 422)
        except Exception as exc:
            return JSONResponse({"error": str(exc)[:400], "code": "failed"}, 502)
        return {"meta": data["meta"], "lang": data.get("lang"), "auto": data.get("auto"), "source": data.get("source"),
                "text": "\n".join(f"[{fmt_ts(s['start'])}] {s['text']}" for s in data["segments"])[:400_000]}
