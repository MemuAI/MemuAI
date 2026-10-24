"""MemuMusic online search, round 41 (r41b-online): where a result comes from and what it is.

* ``search``  — YouTube Music (its own web API «youtubei/v1/search», songs filter: artist, album, duration, plays,
  square cover; yt-dlp is the fallback), YouTube and SoundCloud (yt-dlp flat search: channel, verification, views,
  likes, date). Every result gets ``tags`` from its title/channel (official audio / video, lyric video, live, cover,
  remix, slowed/sped up, karaoke, instrumental, preview) and ``in_library`` (the same original or a similar track).
* ``details`` — lazy, for the «Track info» sheet: full yt-dlp extraction (description, date, likes, audio formats)
  plus album/year/genre/HD cover from the iTunes Search API, Deezer as the fallback. Each part fails on its own
  (YouTube «not a bot» check, no network) — the sheet shows what is known.
* ``related`` — «Similar tracks»: the YouTube Music radio of the song («youtubei/v1/next», RDAMVM<id>) for YouTube,
  ``/recommended`` for SoundCloud.
* ``lyrics``  — a quick lyrics lookup for a track that is not in the library yet (the library's lyrics sources).

Results are cached in memory (search 10 min, details 30 min). Network goes through ``client_factory`` / ``runner_factory``
so the tests run without it.
"""
from __future__ import annotations

import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

from . import sources

SEARCH_SOURCES = ("ytmusic", "youtube", "soundcloud")
_YTM_CLIENT = {"clientName": "WEB_REMIX", "clientVersion": "1.20241023.01.00"}
_YTM_SONGS = "EgWKAQIIAWoQEAMQBBAJEAoQBRAREBAQFQ%3D%3D"   # the «Songs» filter of music.youtube.com search
_SEARCH_TTL, _DETAILS_TTL = 600, 1800

_cache: Dict[tuple, tuple] = {}
_cache_lock = threading.Lock()


def _cached(key: tuple, ttl: float, make: Callable[[], Any]) -> Any:
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = make()
    with _cache_lock:
        if len(_cache) > 300:   # small LRU-ish trim: the oldest half goes
            for old in sorted(_cache, key=lambda k: _cache[k][0])[:150]:
                _cache.pop(old, None)
        _cache[key] = (now, value)
    return value


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


# ---------------------------------------------------------------------- what a result is

_TAG_RULES = [
    ("official_audio", r"official\s+audio|официальн\w*\s+аудио|\(audio\)|\[audio\]"),
    ("official_video", r"official\s+(music\s+)?video|official\s+clip|официальн\w*\s+(видео|клип)|\bm/?v\b"),
    ("lyric", r"lyrics?\s+video|\(lyrics?\)|\[lyrics?\]|текст\s+песни|lyric"),
    ("live", r"\blive\b|концерт|\bлайв\b|живое\s+исполнение|unplugged|\bsession\b"),
    ("cover", r"\bcover\b|кавер|\bперепел"),
    ("remix", r"remix|ремикс|\bbootleg\b|\brework\b|\bvip\s+mix\b"),
    ("slowed", r"slowed|reverb|замедл"),
    ("sped_up", r"sped\s*up|speed\s*up|nightcore|ускор"),
    ("karaoke", r"karaoke|караоке|backing\s+track|минус(овка)?\b"),
    ("instrumental", r"instrumental|инструментал"),
    ("extended", r"extended|расширенн"),
    ("acoustic", r"acoustic|акустик"),
    ("visualizer", r"visuali[sz]er"),
]


def tags_of(title: str, channel: str = "", source: str = "", duration: float = 0, preview: bool = False) -> List[str]:
    """What a result is, from its title and channel: official audio/video, live, cover, remix… (ordered, unique)."""
    text = str(title or "").lower()
    tags = [name for name, rule in _TAG_RULES if re.search(rule, text, re.I)]
    chan = str(channel or "")
    if source == "ytmusic" and "official_audio" not in tags and not ({"live", "cover", "karaoke"} & set(tags)):
        tags.insert(0, "official_audio")   # a YouTube Music «song» is the label's audio track
    elif re.search(r"-\s*Topic$", chan) and "official_audio" not in tags:
        tags.insert(0, "official_audio")   # «Artist - Topic» channels are auto-generated official audio
    elif re.search(r"vevo$", chan, re.I) and "official_video" not in tags and "official_audio" not in tags:
        tags.insert(0, "official_video")
    if preview:
        tags.append("preview")
    if "official_audio" in tags and "official_video" in tags:
        tags.remove("official_video")
    return tags


def _num(value: Any) -> int:
    """«852M plays» / «1.2K» / 1234 → int."""
    if isinstance(value, (int, float)):
        return int(value)
    m = re.search(r"([\d.,]+)\s*([KMB])?", str(value or ""), re.I)
    if not m:
        return 0
    try:
        n = float(m.group(1).replace(",", ""))
    except ValueError:
        return 0
    return int(n * {"K": 1e3, "M": 1e6, "B": 1e9}.get((m.group(2) or "").upper(), 1))


def _secs(text: str) -> float:
    parts = [p for p in str(text or "").strip().split(":") if p.isdigit()]
    if not parts or len(parts) > 3:
        return 0
    total = 0
    for p in parts:
        total = total * 60 + int(p)
    return float(total)


def _date(entry: Dict[str, Any]) -> str:
    """ISO date from yt-dlp fields (upload_date YYYYMMDD / release_date / timestamp)."""
    for key in ("release_date", "upload_date"):
        raw = str(entry.get(key) or "")
        if re.fullmatch(r"\d{8}", raw):
            return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    ts = entry.get("release_timestamp") or entry.get("timestamp")
    if isinstance(ts, (int, float)) and ts > 0:
        return time.strftime("%Y-%m-%d", time.gmtime(ts))
    return ""


def _item(**fields: Any) -> Dict[str, Any]:
    base = {"id": "", "url": "", "title": "", "artist": "", "raw_title": "", "album": "", "year": "", "duration": 0,
            "thumb": "", "source": "", "channel": "", "channel_url": "", "verified": False, "views": 0, "likes": 0,
            "published": "", "explicit": False, "preview": False, "tags": [], "in_library": ""}
    base.update({k: v for k, v in fields.items() if v is not None})
    base["duration"] = float(base["duration"] or 0)
    if not base["tags"]:
        base["tags"] = tags_of(base["raw_title"] or base["title"], base["channel"], base["source"], base["duration"], base["preview"])
    return base


def _ytm_thumb(thumbs: List[Dict[str, Any]], size: int = 544) -> str:
    if not thumbs:
        return ""
    url = str(thumbs[-1].get("url") or "")
    return re.sub(r"=w\d+-h\d+", f"=w{size}-h{size}", url) if "googleusercontent" in url else url


# ---------------------------------------------------------------------- YouTube Music web API

def _walk(obj: Any, key: str):
    if isinstance(obj, dict):
        if key in obj:
            yield obj[key]
        for value in obj.values():
            yield from _walk(value, key)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk(value, key)


def _innertube(endpoint: str, payload: Dict[str, Any], client_factory=None) -> Dict[str, Any]:
    body = {"context": {"client": {**_YTM_CLIENT, "hl": "en"}}, **payload}
    client = (client_factory or sources._client)()
    try:
        resp = client.post(f"https://music.youtube.com/youtubei/v1/{endpoint}?prettyPrint=false", json=body,
                           headers={"Origin": "https://music.youtube.com", "Referer": "https://music.youtube.com/"})
        if getattr(resp, "status_code", 200) >= 400:
            raise RuntimeError(f"YouTube Music HTTP {resp.status_code}")
        return resp.json()
    finally:
        try:
            client.close()
        except Exception:
            pass


def _runs(col: Dict[str, Any]) -> List[Dict[str, Any]]:
    return ((col.get("musicResponsiveListItemFlexColumnRenderer") or {}).get("text") or {}).get("runs") or []


def _page_type(run: Dict[str, Any]) -> str:
    ep = (run.get("navigationEndpoint") or {}).get("browseEndpoint") or {}
    return (((ep.get("browseEndpointContextSupportedConfigs") or {}).get("browseEndpointContextMusicConfig") or {})
            .get("pageType") or "")


def ytm_search(q: str, limit: int = 12, client_factory=None) -> List[Dict[str, Any]]:
    data = _innertube("search", {"query": q, "params": _YTM_SONGS}, client_factory)
    out = []
    for it in _walk(data, "musicResponsiveListItemRenderer"):
        vid = ((it.get("playlistItemData") or {}).get("videoId")
               or next((r["navigationEndpoint"]["watchEndpoint"].get("videoId") for c in it.get("flexColumns") or []
                        for r in _runs(c) if (r.get("navigationEndpoint") or {}).get("watchEndpoint")), ""))
        cols = it.get("flexColumns") or []
        if not vid or len(cols) < 2:
            continue
        title = "".join(r.get("text", "") for r in _runs(cols[0]))
        meta = [r for r in _runs(cols[1]) if r.get("text", "").strip() not in ("•", "")]
        artists = [r["text"] for r in meta if _page_type(r) == "MUSIC_PAGE_TYPE_ARTIST"]
        album = next((r["text"] for r in meta if _page_type(r) == "MUSIC_PAGE_TYPE_ALBUM"), "")
        duration = next((_secs(r["text"]) for r in meta if re.fullmatch(r"\d+(:\d\d){1,2}", r["text"].strip())), 0)
        if not artists:   # plain text runs: «Artist • Album • 3:45»
            plain = [r["text"] for r in meta if not re.fullmatch(r"\d+(:\d\d){1,2}", r["text"].strip())]
            artists = plain[:1]
        plays = _num("".join(r.get("text", "") for c in cols[2:] for r in _runs(c)))
        thumb = _ytm_thumb((((it.get("thumbnail") or {}).get("musicThumbnailRenderer") or {}).get("thumbnail") or {}).get("thumbnails") or [])
        explicit = "EXPLICIT" in str(it.get("badges") or "").upper()
        out.append(_item(id=vid, url="https://music.youtube.com/watch?v=" + vid, title=title, raw_title=title,
                         artist=", ".join(artists), album=album, duration=duration, thumb=thumb, source="ytmusic",
                         channel=artists[0] if artists else "", views=plays, explicit=explicit, verified=bool(artists)))
        if len(out) >= limit:
            break
    return out


def ytm_related(video_id: str, limit: int = 20, client_factory=None) -> List[Dict[str, Any]]:
    data = _innertube("next", {"videoId": video_id, "playlistId": "RDAMVM" + video_id, "isAudioOnly": True}, client_factory)
    out = []
    for v in _walk(data, "playlistPanelVideoRenderer"):
        vid = v.get("videoId") or ""
        if not vid or vid == video_id:
            continue
        title = "".join(r.get("text", "") for r in (v.get("title") or {}).get("runs") or [])
        byline = "".join(r.get("text", "") for r in (v.get("longBylineText") or {}).get("runs") or [])   # «A, B • Album • 2013»
        parts = [p.strip() for p in byline.split("•") if p.strip()]
        year = next((p for p in parts if re.fullmatch(r"(19|20)\d\d", p)), "")
        rest = [p for p in parts if p != year and not re.search(r"(views|просмотр|plays)", p, re.I)]
        duration = _secs("".join(r.get("text", "") for r in (v.get("lengthText") or {}).get("runs") or []))
        out.append(_item(id=vid, url="https://music.youtube.com/watch?v=" + vid, title=title, raw_title=title,
                         artist=rest[0] if rest else "", album=rest[1] if len(rest) > 1 else "", year=year,
                         duration=duration, source="ytmusic", channel=rest[0] if rest else "",
                         thumb=_ytm_thumb(((v.get("thumbnail") or {}).get("thumbnails")) or [])))
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------- yt-dlp search (YouTube, SoundCloud, fallback)

def _from_ytdlp(e: Dict[str, Any], source: str) -> Optional[Dict[str, Any]]:
    from .downloader import split_title
    if not e or not e.get("id"):
        return None
    uploader = e.get("channel") or e.get("uploader") or ""
    raw = e.get("title") or ""
    names = split_title(raw, uploader)
    thumbs = [t for t in e.get("thumbnails") or [] if t.get("url")]
    if source == "soundcloud":
        url = e.get("webpage_url") or (e.get("url") if "api.soundcloud" not in str(e.get("url") or "") else "") or ""
        artists = e.get("artists") or []
        thumb = max(thumbs, key=lambda t: (t.get("width") or 0)).get("url", "") if thumbs else ""
        duration = float(e.get("duration") or 0)
        return _item(id=str(e["id"]), url=url, title=e.get("track") or (raw if uploader else names["title"]), raw_title=raw,
                     artist=(artists[0] if artists else uploader or names["artist"]), duration=duration, thumb=thumb, source="soundcloud",
                     channel=uploader, channel_url=e.get("uploader_url") or "", views=e.get("view_count") or 0,
                     likes=e.get("like_count") or 0, published=_date(e), preview=0 < duration <= 30.5,
                     year=_date(e)[:4] if e.get("genres") else "", album="")
    url = e.get("url") if str(e.get("url") or "").startswith("http") else "https://www.youtube.com/watch?v=" + e["id"]
    if source == "ytmusic" and "music.youtube.com" not in url:
        url = "https://music.youtube.com/watch?v=" + e["id"]
    thumb = thumbs[-1]["url"] if thumbs else f"https://i.ytimg.com/vi/{e['id']}/hqdefault.jpg"
    live = e.get("live_status") in ("is_live", "was_live")
    item = _item(id=e["id"], url=url, title=names["title"], raw_title=raw, artist=names["artist"],
                 duration=e.get("duration") or 0, thumb=thumb, source=source, channel=uploader,
                 channel_url=e.get("channel_url") or e.get("uploader_url") or "", verified=bool(e.get("channel_is_verified")),
                 views=e.get("view_count") or 0, published=_date(e))
    if live and "live" not in item["tags"]:
        item["tags"].append("live")
    return item


def ytdlp_search(q: str, limit: int, source: str, runner_factory=None) -> List[Dict[str, Any]]:
    from .downloader import Runner, _quote
    runner = (runner_factory or Runner)()
    if source == "soundcloud":
        data = runner.json(f"scsearch{limit}:{q}", flat=True, timeout=60)
    elif source == "ytmusic":
        data = runner.json("https://music.youtube.com/search?q=" + _quote(q) + "#songs", flat=True, limit=limit, timeout=60)
    else:
        data = runner.json(f"ytsearch{limit}:{q}", flat=True, timeout=60)
    return [x for x in (_from_ytdlp(e, source) for e in (data.get("entries") or [])[:limit]) if x]


def mark_library(lib, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """``in_library``: id of the same original (source key) or of a similar track (title + artist + duration)."""
    from .downloader import source_key
    for it in items:
        found = None
        try:
            found = lib.find_by_source(source_key(it["url"])) if it.get("url") else None
            if not found and it.get("title") and it.get("artist"):
                found = lib.find_similar(it["title"], it["artist"], it.get("duration") or 0)
        except Exception:
            found = None
        it["in_library"] = (found or {}).get("id", "") if found else ""
    return items


def search(lib, q: str, limit: int = 12, source: str = "ytmusic", client_factory=None, runner_factory=None) -> Dict[str, Any]:
    """{results, source, via, error}: ``via`` says which method answered (``ytmusic-api`` or ``yt-dlp``)."""
    q = str(q or "").strip()
    source = source if source in SEARCH_SOURCES else "ytmusic"
    limit = max(1, min(30, int(limit or 12)))
    if not q:
        return {"results": [], "source": source, "via": "", "error": ""}

    def make() -> Dict[str, Any]:
        error = ""
        if source == "ytmusic":
            try:
                found = ytm_search(q, limit, client_factory)
                if found:
                    return {"results": found, "source": source, "via": "ytmusic-api", "error": ""}
            except Exception as exc:   # the web API changed or is blocked — yt-dlp still knows the way
                error = str(exc)[:200]
        try:
            return {"results": ytdlp_search(q, limit, source, runner_factory), "source": source, "via": "yt-dlp", "error": ""}
        except Exception as exc:
            return {"results": [], "source": source, "via": "", "error": (str(exc) or error)[:300]}

    answer = _cached(("search", source, q.lower(), limit), _SEARCH_TTL, make)
    if answer.get("error") and not answer["results"]:   # failures are not cached
        with _cache_lock:
            _cache.pop(("search", source, q.lower(), limit), None)
    out = {**answer, "results": [dict(x, tags=list(x["tags"])) for x in answer["results"]]}
    mark_library(lib, out["results"])
    return out


# ---------------------------------------------------------------------- details (the «Track info» sheet)

def _formats(info: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Audio-only formats, best first: ext, codec, kbps, kHz, size."""
    out, seen = [], set()
    for f in info.get("formats") or []:
        if (f.get("vcodec") not in (None, "none")) or f.get("acodec") in (None, "none"):
            continue
        codec = str(f.get("acodec") or "").split(".")[0].replace("mp4a", "aac")
        kbps = int(round(float(f.get("abr") or f.get("tbr") or 0)))
        key = (f.get("ext"), codec, kbps)
        if key in seen:
            continue
        seen.add(key)
        out.append({"ext": f.get("ext") or "", "codec": codec, "kbps": kbps, "khz": round((f.get("asr") or 0) / 1000, 1),
                    "size": int(f.get("filesize") or f.get("filesize_approx") or 0), "note": f.get("format_note") or ""})
    out.sort(key=lambda x: -x["kbps"])
    return out[:6]


def _full_info(url: str, runner_factory=None) -> Dict[str, Any]:
    from .downloader import Runner
    info = (runner_factory or Runner)().json(url, flat=False, timeout=45)
    if not isinstance(info, dict) or not info:   # «--ignore-errors» answers null (YouTube «not a bot», region, removed)
        raise RuntimeError("blocked: the source gave no details")
    desc = str(info.get("description") or "")
    return {"description": desc[:4000], "published": _date(info), "likes": int(info.get("like_count") or 0),
            "views": int(info.get("view_count") or 0), "channel": info.get("channel") or info.get("uploader") or "",
            "channel_url": info.get("channel_url") or info.get("uploader_url") or "",
            "followers": int(info.get("channel_follower_count") or 0), "verified": bool(info.get("channel_is_verified")),
            "formats": _formats(info), "album": info.get("album") or "", "artist_tag": info.get("artist") or ", ".join(info.get("artists") or []),
            "track_tag": info.get("track") or "", "year": str(info.get("release_year") or "")[:4],
            "genre": ", ".join(info.get("genres") or []) or "", "categories": info.get("categories") or [],
            "duration": float(info.get("duration") or 0), "thumb": info.get("thumbnail") or ""}


def _similar(a: str, b: str) -> bool:
    from .lyrics import similarity
    return similarity(a, b) >= 0.6


def enrich(title: str, artist: str, client_factory=None) -> Dict[str, Any]:
    """Album, year, genre and an HD cover: iTunes Search API, Deezer as the fallback. No keys; errors → {}."""
    from .lyrics import clean_title
    title, artist = clean_title(title or ""), str(artist or "").split(",")[0].strip()
    if not title:
        return {}
    client = (client_factory or sources._client)()
    try:
        try:
            data = client.get("https://itunes.apple.com/search?" + urllib.parse.urlencode(
                {"term": f"{artist} {title}".strip(), "media": "music", "entity": "song", "limit": 8})).json()
            for r in data.get("results") or []:
                if _similar(r.get("trackName") or "", title) and (not artist or _similar(r.get("artistName") or "", artist)):
                    art = str(r.get("artworkUrl100") or "")
                    return {"album": r.get("collectionName") or "", "year": str(r.get("releaseDate") or "")[:4],
                            "genre": r.get("primaryGenreName") or "", "cover": art.replace("100x100bb", "1000x1000bb"),
                            "track_no": r.get("trackNumber") or 0, "explicit": r.get("trackExplicitness") == "explicit",
                            "meta_source": "itunes", "meta_url": r.get("trackViewUrl") or ""}
        except Exception:
            pass
        try:
            data = client.get("https://api.deezer.com/search?" + urllib.parse.urlencode(
                {"q": f'artist:"{artist}" track:"{title}"' if artist else title, "limit": 8})).json()
            for r in data.get("data") or []:
                if _similar(r.get("title") or "", title):
                    album = r.get("album") or {}
                    return {"album": album.get("title") or "", "year": "", "genre": "", "cover": album.get("cover_xl") or "",
                            "explicit": bool(r.get("explicit_lyrics")), "meta_source": "deezer", "meta_url": r.get("link") or ""}
        except Exception:
            pass
    finally:
        try:
            client.close()
        except Exception:
            pass
    return {}


def _error_code(text: str) -> str:
    low = str(text or "").lower()
    if "not a bot" in low or "sign in to confirm" in low:
        return "bot"
    if "private" in low or "unavailable" in low or "removed" in low:
        return "unavailable"
    if low.startswith("blocked"):
        return "blocked"
    if "timed out" in low or "timeout" in low:
        return "timeout"
    return "failed"


def details(lib, url: str, hint: Optional[Dict[str, Any]] = None, client_factory=None, runner_factory=None) -> Dict[str, Any]:
    """Everything known about one online result. The two network parts run in parallel; each may fail alone."""
    url = str(url or "").strip()
    if not url.startswith(("https://", "http://")):
        raise ValueError("Нужна ссылка / A link is needed")
    hint = dict(hint or {})

    def make() -> Dict[str, Any]:
        out: Dict[str, Any] = {"url": url, "errors": {}}
        pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="memumusic-online")
        full = pool.submit(_full_info, url, runner_factory)
        meta = pool.submit(enrich, hint.get("title") or "", hint.get("artist") or "", client_factory)
        try:
            out["info"] = full.result(timeout=60)
        except Exception as exc:
            out["info"] = {}
            out["errors"]["info"] = _error_code(str(exc))
        try:
            out["meta"] = meta.result(timeout=30) or {}
        except Exception:
            out["meta"] = {}
        pool.shutdown(wait=False)
        return out

    raw = _cached(("details", url, (hint.get("title") or "").lower()), _DETAILS_TTL, make)
    if raw["errors"].get("info") in ("timeout", "failed") and not raw.get("info"):
        with _cache_lock:   # a transient failure is retried next time
            _cache.pop(("details", url, (hint.get("title") or "").lower()), None)
    info, meta = raw.get("info") or {}, raw.get("meta") or {}
    merged = {
        "url": url, "source": hint.get("source") or sources.detect(url).get("source") or "web",
        "title": hint.get("title") or info.get("track_tag") or "", "artist": hint.get("artist") or info.get("artist_tag") or "",
        "album": hint.get("album") or info.get("album") or meta.get("album") or "",
        "year": hint.get("year") or info.get("year") or meta.get("year") or (info.get("published") or "")[:4],
        "genre": info.get("genre") or meta.get("genre") or "",
        "duration": float(hint.get("duration") or info.get("duration") or 0),
        "channel": info.get("channel") or hint.get("channel") or "", "channel_url": info.get("channel_url") or hint.get("channel_url") or "",
        "verified": bool(info.get("verified") or hint.get("verified")), "followers": info.get("followers") or 0,
        "published": info.get("published") or hint.get("published") or "",
        "views": info.get("views") or int(hint.get("views") or 0), "likes": info.get("likes") or int(hint.get("likes") or 0),
        "description": info.get("description") or "", "formats": info.get("formats") or [],
        "cover": meta.get("cover") or "", "thumb": hint.get("thumb") or info.get("thumb") or "",
        "explicit": bool(hint.get("explicit") or meta.get("explicit")), "meta_source": meta.get("meta_source") or "",
        "meta_url": meta.get("meta_url") or "", "categories": info.get("categories") or [],
        "errors": dict(raw["errors"]), "related": hint.get("source") in ("ytmusic", "youtube", "soundcloud"),
    }
    merged["tags"] = tags_of(hint.get("raw_title") or merged["title"], merged["channel"], merged["source"], merged["duration"],
                             bool(hint.get("preview")))
    mark_library(lib, [merged])
    return merged


def related(lib, url: str, limit: int = 20, client_factory=None, runner_factory=None) -> List[Dict[str, Any]]:
    """«Similar tracks» the source itself suggests."""
    info = sources.detect(url)
    limit = max(1, min(40, int(limit or 20)))

    def make() -> List[Dict[str, Any]]:
        if info.get("source") in ("youtube", "ytmusic") and info.get("id"):
            return ytm_related(info["id"], limit, client_factory)
        if info.get("source") == "soundcloud":
            from .downloader import Runner
            data = (runner_factory or Runner)().json(re.sub(r"[?#].*$", "", url).rstrip("/") + "/recommended",
                                                     flat=True, limit=limit, timeout=60)
            return [x for x in (_from_ytdlp(e, "soundcloud") for e in (data.get("entries") or [])[:limit]) if x]
        return []

    found = _cached(("related", url, limit), _DETAILS_TTL, make)
    return mark_library(lib, [dict(x, tags=list(x["tags"])) for x in found])


def lyrics_lookup(lib, title: str, artist: str, duration: float = 0, client_factory=None) -> Dict[str, Any]:
    """Lyrics for a track that is not downloaded yet: the best candidate of the library's lyrics sources."""
    from . import lyrics
    q = lyrics.query_of({"title": title, "artist": artist, "duration": duration})
    chosen = [s for s in (lib.settings().get("lyrics_sources") or []) if s in lyrics.PROVIDERS] or ["lrclib"]

    def make() -> Dict[str, Any]:
        found, _status = lyrics.search(q, chosen, client_factory)
        best = found[0] if found else None
        if not best:
            return {"found": False}
        text = best.get("plain") or "\n".join(line for _sec, line in (best.get("synced") or []))
        return {"found": True, "text": text[:6000], "synced": bool(best.get("synced")), "source": best.get("source") or "",
                "instrumental": bool(best.get("instrumental"))}

    return _cached(("lyrics", q["title"].lower(), q["artist"].lower()), _DETAILS_TTL, make)
