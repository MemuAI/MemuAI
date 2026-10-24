# -*- coding: utf-8 -*-
"""Lyrics from several free sources (no keys), cached in the library.

r39: one search asks every enabled source in parallel (each with its own timeout), scores every candidate by title,
artist (noise like «(Official Video)», «feat.», «- Topic» removed, Cyrillic transliterated) and duration (±2 s is a
match), and keeps the whole list: the best one is shown, «Wrong lyrics» moves to the next one, the user can pick any.
Synced sources: LRCLIB, Musixmatch (desktop token, richsync = word timing), NetEase (yrc = word timing), QQ Music; plain
text: Genius, lyrics.ovh — used only when no synced source found anything (setting ``lyrics_plain_fallback``).
A synced line is ``[sec, text]`` or ``[sec, text, [[sec, word], …]]`` (word timing from enhanced LRC / richsync / yrc).
r41b (lyrics sync): when the source knows where the SUNG part of a line ends, a 4th item — ``[sec, text, words|None, end]``
(enhanced LRC trailing ``<mm:ss.xx>`` / ``[mm:ss.xx]``, richsync ``te``, yrc line duration); a word may carry its end
too — ``[sec, word, end]``. An empty line ``[mm:ss.xx]`` stays a row with empty text: the end of the previous line (a
pause). Old clients read only ``row[0..2]``."""
from __future__ import annotations

import difflib
import html
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any, Callable, Dict, List, Optional, Tuple

from .library import Library

API = "https://lrclib.net/api"
#: An empty answer is cached too, but retried after a week (new lyrics appear all the time).
MISS_TTL = 7 * 86400
SOURCES = ("lrclib", "musixmatch", "netease", "qq", "genius", "lyricsovh")
SYNCED_SOURCES = ("lrclib", "musixmatch", "netease", "qq")
SOURCE_NAMES = {"lrclib": "LRCLIB", "musixmatch": "Musixmatch", "netease": "NetEase", "qq": "QQ Music",
                "genius": "Genius", "lyricsovh": "lyrics.ovh", "manual": "manual"}
DEADLINE = 12.0          # seconds for the whole parallel search
MAX_ALTS = 12
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
_LINE = re.compile(r"^\s*((?:\[\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?\])+)(.*)$")
_STAMP = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_WORD = re.compile(r"<(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?>")
_TAIL = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]\s*$")   # r41b: «[00:01.00]text[00:05.20]» — where the line ends
_OFFSET = re.compile(r"^\s*\[offset:\s*([+-]?\d+)\s*\]", re.I | re.M)
#: credit lines Chinese sources put at the top («作词 : …», «Lyrics by：…»)
_CREDIT = re.compile(r"^\s*(作词|作曲|编曲|制作人|制作|混音|母带|和声|吉他|贝斯|鼓|监制|出品|录音|词|曲|"
                     r"lyrics by|lyricist|composed by|composer|produced by|producer|arranged by|written by|mixed by|"
                     r"mastered by|recorded by)\s*[:：]", re.I)


def _sec(mm: str, ss: str, frac: str) -> float:
    return int(mm) * 60 + int(ss) + (int(frac) / (10 ** len(frac)) if frac else 0)


def parse_lrc(text: Optional[str]) -> Optional[List[list]]:
    """LRC → ``[[sec, line], …]``; enhanced LRC (``<mm:ss.xx>`` before words) adds ``[[sec, word], …]``."""
    if not text:
        return None
    m = _OFFSET.search(text)
    shift = -int(m.group(1)) / 1000 if m else 0.0   # [offset:+500] = lyrics come 0.5 s earlier
    out: List[list] = []
    for raw in text.splitlines():
        m = _LINE.match(raw)
        if not m:
            continue
        body = m.group(2)
        words: List[list] = []
        end: Optional[float] = None
        tail = _TAIL.search(body)
        if tail and body[:tail.start()].strip():
            end = round(max(0.0, _sec(*tail.groups(default="")) + shift), 2)
            body = body[:tail.start()]
        if _WORD.search(body):
            parts = _WORD.split(body)       # [before, mm, ss, frac, text, mm, ss, frac, text, …]
            for i in range(1, len(parts) - 3, 4):
                word, at = parts[i + 3], round(max(0.0, _sec(parts[i], parts[i + 1], parts[i + 2]) + shift), 2)
                if word.strip():
                    words.append([at, word])
                elif words and i + 4 >= len(parts) - 3:
                    end = at                 # r41b: a trailing «<mm:ss.xx>» after the last word = the end of the line
            body = _WORD.sub("", body)
        line = re.sub(r"\s+", " ", body).strip()
        for mm, ss, frac in _STAMP.findall(m.group(1)):
            row: list = [round(max(0.0, _sec(mm, ss, frac) + shift), 2), line]
            if words or end is not None:
                row.append(words or None)
            if end is not None and end > row[0]:
                row.append(end)
            elif end is not None and not words:
                row.pop()
            out.append(row)
    out.sort(key=lambda x: x[0])
    return out or None


_YRC_LINE = re.compile(r"^\[(\d+),(\d+)\](.*)$")
_YRC_WORD = re.compile(r"\((\d+),(\d+),\d+\)")


def parse_yrc(text: Optional[str]) -> Optional[List[list]]:
    """NetEase «yrc»: ``[startMs,durMs](wordStartMs,wordDurMs,0)word(…)word`` → ``[sec, line, [[sec, word, end]…], end]``.
    JSON lines (credits ``{"t":…,"c":[…]}``) and lines without words are skipped."""
    out: List[list] = []
    for raw in (text or "").splitlines():
        m = _YRC_LINE.match(raw.strip())
        if not m:
            continue
        start, dur = int(m.group(1)) / 1000, int(m.group(2)) / 1000
        parts = _YRC_WORD.split(m.group(3))   # [before, ms, dur, word, ms, dur, word, …]
        words = [[round(int(parts[i]) / 1000, 2), parts[i + 2], round((int(parts[i]) + int(parts[i + 1])) / 1000, 2)]
                 for i in range(1, len(parts) - 2, 3) if parts[i + 2].strip()]
        line = re.sub(r"\s+", " ", "".join(w[1] for w in words)).strip()
        if not line:
            continue
        out.append([round(start, 2), line, words, round(start + dur, 2)] if dur > 0 else [round(start, 2), line, words])
    out.sort(key=lambda x: x[0])
    return out or None


def _strip_credits(synced: Optional[List[list]], title: str = "") -> Optional[List[list]]:
    """Drop credit lines and the «Title - Artist» header from the first seconds of NetEase / QQ lyrics."""
    if not synced:
        return synced
    head = norm(title)
    out = []
    for i, row in enumerate(synced):
        line = row[1]
        if i < 12 and (_CREDIT.match(line) or (head and i < 2 and " - " in line and norm(line.split(" - ")[0]) == head)):
            continue
        out.append(row)
    return out if len(out) >= 2 else None


# ---------------------------------------------------------------------- matching

_TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                     ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "i", "k", "l", "m", "n", "o", "p", "r", "s",
                      "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))
_NOISE = r"official|video|audio|lyrics?|lyric video|clip|клип|remaster(?:ed)?|hd|hq|4k|visualizer|mv|m/v|live|текст|премьера|explicit"


def clean_title(title: str) -> str:
    """«Song (Official Video) [Lyrics] (feat. X)» → «Song»."""
    text = str(title or "")
    text = re.sub(r"\s*[\(\[【][^\)\]】]*(?:" + _NOISE + r"|feat|ft\.|prod\.)[^\)\]】]*[\)\]】]", "", text, flags=re.I)
    text = re.sub(r"\s+(?:feat\.?|ft\.?|featuring)\s+.*$", "", text, flags=re.I)
    text = re.sub(r"\s*[|｜].*$", "", text)
    return re.sub(r"\s+", " ", text).strip(" -–—") or str(title or "").strip()


def clean_artist(artist: str) -> str:
    text = re.sub(r"\s*-\s*topic$", "", str(artist or ""), flags=re.I)
    text = re.split(r"\s*(?:,|&|\bfeat\.?|\bft\.?|\bx\b|;|/)\s*", text, flags=re.I)[0]
    return re.sub(r"\s*vevo$", "", text, flags=re.I).strip()


def query_of(row: Dict[str, Any]) -> Dict[str, Any]:
    title, artist = clean_title(row.get("title") or ""), clean_artist(row.get("artist") or "")
    # YouTube uploads: «Artist - Song» in the title, the channel as the artist
    if " - " in title:
        left, right = [x.strip() for x in title.split(" - ", 1)]
        if not artist or norm(artist) in norm(left) or norm(left) in norm(artist):
            artist, title = clean_artist(left) or artist, clean_title(right)
    return {"title": title, "artist": artist, "album": row.get("album") or "", "duration": float(row.get("duration") or 0)}


def norm(value: str) -> str:
    text = str(value or "").lower().replace("ё", "е")
    text = "".join(_TRANSLIT.get(ch, ch) for ch in text)
    text = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", text)
    return re.sub(r"[^\w]+", " ", text, flags=re.UNICODE).replace("_", " ").strip()


def similarity(a: str, b: str) -> float:
    x, y = norm(a), norm(b)
    if not x or not y:
        return 0.0
    if x == y:
        return 1.0
    if (len(x) >= 3 and x in y) or (len(y) >= 3 and y in x):
        return 0.9
    return difflib.SequenceMatcher(None, x, y).ratio()


def score(cand: Dict[str, Any], q: Dict[str, Any]) -> float:
    """0..~1.4: how well a candidate fits the track. Below 0.55 it is not this song."""
    t = similarity(cand.get("title") or "", q["title"])
    a = similarity(cand.get("artist") or "", q["artist"]) if q["artist"] and cand.get("artist") else 0.6
    if t < 0.6 or (q["artist"] and cand.get("artist") and a < 0.35):
        return 0.0
    dur, want = float(cand.get("duration") or 0), q["duration"]
    if dur and want:
        diff = abs(dur - want)
        if diff > 20:
            return 0.0
        d = 1.0 if diff <= 2 else 0.6 if diff <= 5 else 0.25
    else:
        d = 0.45
    value = t * 0.45 + a * 0.3 + d * 0.25
    if cand.get("synced"):
        value += 0.2 if d >= 1.0 else 0.08
        if any(len(row) > 2 and row[2] for row in cand["synced"]):
            value += 0.03
    return round(value, 4)


def _plausible(text: str, duration: float) -> bool:
    lines = [l for l in (text or "").splitlines() if l.strip()]
    return bool(text) and (len(lines) >= 4 or len(text) >= 80 or bool(duration and duration < 60))


def _cand(source: str, title: str, artist: str, duration: float, synced: Optional[list], plain: Optional[str],
          instrumental: bool = False) -> Optional[Dict[str, Any]]:
    if synced and not plain:
        plain = "\n".join(row[1] for row in synced)
    if not instrumental and not (synced and len(synced) >= 2) and not _plausible(plain or "", duration):
        return None
    return {"source": source, "title": title or "", "artist": artist or "", "duration": round(float(duration or 0), 1),
            "synced": synced if synced and len(synced) >= 2 else None, "plain": (plain or "").strip() or None,
            "instrumental": bool(instrumental)}


# ---------------------------------------------------------------------- providers (each: client, query -> candidates)

def _lrclib(client, q) -> List[Dict[str, Any]]:
    hits: List[Dict[str, Any]] = []
    params = {"track_name": q["title"], "artist_name": q["artist"]}
    if q["album"]:
        params["album_name"] = q["album"]
    if q["duration"]:
        params["duration"] = str(int(round(q["duration"])))
    if q["artist"]:
        r = client.get(f"{API}/get", params=params)
        if r.status_code == 200 and isinstance(r.json(), dict):
            hits.append(r.json())
    exact = hits and len(parse_lrc(hits[0].get("syncedLyrics")) or []) >= 4 and (not q["duration"] or abs(float(hits[0].get("duration") or 0) - q["duration"]) <= 2)
    r = None if exact else client.get(f"{API}/search", params={"track_name": q["title"], "artist_name": q["artist"]} if q["artist"]
                   else {"q": q["title"]})
    if r is None:
        pass   # /get gave the synced lyrics with the same length: no need for /search
    elif r.status_code == 200 and isinstance(r.json(), list):
        hits += r.json()[:10]
    elif r.status_code >= 500 and not hits:
        raise RuntimeError(f"LRCLIB {r.status_code}")
    out, seen = [], set()
    for h in hits:
        if not isinstance(h, dict) or (h.get("id") and h["id"] in seen):
            continue
        seen.add(h.get("id"))
        c = _cand("lrclib", h.get("trackName") or h.get("name") or q["title"], h.get("artistName") or q["artist"], h.get("duration") or 0,
                  parse_lrc(h.get("syncedLyrics")), h.get("plainLyrics"), bool(h.get("instrumental")))
        if c:
            out.append(c)
    return out


_MXM = "https://apic-desktop.musixmatch.com/ws/1.1/"
_MXM_TOKEN: Dict[str, Any] = {"token": "", "at": 0.0}


def _mxm_get(client, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    r = client.get(_MXM + method, params={**params, "app_id": "web-desktop-app-v1.0", "format": "json"},
                   headers={"Cookie": "AWSELBCORS=0; AWSELB=0", "authority": "apic-desktop.musixmatch.com"})
    data = r.json() if r.status_code == 200 else {}
    return ((data or {}).get("message") or {})


def _musixmatch(client, q) -> List[Dict[str, Any]]:
    token = _MXM_TOKEN["token"] if time.time() - _MXM_TOKEN["at"] < 600 else ""
    if not token:
        body = _mxm_get(client, "token.get", {"user_language": "en", "t": str(int(time.time() * 1000))}).get("body") or {}
        token = str(body.get("user_token") or "") if isinstance(body, dict) else ""
        if not token or set(token) <= {"0"} or "Upgrade" in token:
            raise RuntimeError("Musixmatch token unavailable (captcha)")
        _MXM_TOKEN.update(token=token, at=time.time())
    body = _mxm_get(client, "track.search", {"q_track": q["title"], "q_artist": q["artist"], "page_size": 5,
                                             "page": 1, "s_track_rating": "desc", "usertoken": token}).get("body") or {}
    out = []
    for item in (body.get("track_list") or [])[:3] if isinstance(body, dict) else []:
        tr = (item or {}).get("track") or {}
        cand = {"title": tr.get("track_name"), "artist": tr.get("artist_name"), "duration": tr.get("track_length")}
        if not tr.get("has_subtitles") and not tr.get("has_richsync") or score({**cand, "synced": None}, q) <= 0:
            continue
        synced = None
        if tr.get("has_richsync"):
            rich = (_mxm_get(client, "track.richsync.get", {"track_id": tr["track_id"], "usertoken": token}).get("body") or {})
            synced = _richsync(((rich or {}).get("richsync") or {}).get("richsync_body"))
        if not synced:
            sub = (_mxm_get(client, "track.subtitle.get", {"track_id": tr["track_id"], "subtitle_format": "lrc",
                                                         "usertoken": token}).get("body") or {})
            synced = parse_lrc(((sub or {}).get("subtitle") or {}).get("subtitle_body"))
        c = _cand("musixmatch", cand["title"] or "", cand["artist"] or "", cand["duration"] or 0, synced, None,
                  bool(tr.get("instrumental")))
        if c:
            out.append(c)
            break
    return out


def _richsync(body: Optional[str]) -> Optional[List[list]]:
    """Musixmatch richsync JSON: ``[{ts, te, x, l: [{c, o}]}]`` → lines with word timing."""
    try:
        rows = json.loads(body) if body else None
    except ValueError:
        return None
    out = []
    for row in rows or []:
        try:
            start = float(row["ts"])
            words: List[list] = []
            for w in row.get("l") or []:
                c = str(w.get("c") or "")
                if c.strip():
                    words.append([round(start + float(w.get("o") or 0), 2), c])
                elif words:
                    words[-1][1] += c        # r41b: the spaces stay with the word before (the window joins words as is)
            line = [round(start, 2), re.sub(r"\s+", " ", str(row.get("x") or "")).strip(), words or None]
            end = float(row.get("te") or 0)
            if end > start:
                line.append(round(end, 2))   # r41b: richsync knows where the sung line ends
            out.append(line if words or len(line) > 3 else line[:2])
        except (KeyError, TypeError, ValueError):
            continue
    return out or None


def _netease(client, q) -> List[Dict[str, Any]]:
    head = {"Referer": "https://music.163.com", "Cookie": "appver=2.0.2; os=pc"}
    r = client.get("https://music.163.com/api/cloudsearch/pc", params={"s": f"{q['artist']} {q['title']}".strip(),
                                                                      "type": 1, "limit": 8}, headers=head)
    songs = (((r.json() if r.status_code == 200 else {}) or {}).get("result") or {}).get("songs") or []
    found = []
    for s in songs:
        cand = {"title": s.get("name") or "", "artist": ", ".join(a.get("name") or "" for a in s.get("ar") or []),
                "duration": (s.get("dt") or 0) / 1000}
        if score({**cand, "artist": (s.get("ar") or [{}])[0].get("name") or ""}, q) > 0:
            found.append(({**cand, "artist": (s.get("ar") or [{}])[0].get("name") or ""}, s.get("id")))
    out = []
    for cand, sid in found[:2]:
        lr = client.get("https://music.163.com/api/song/lyric", params={"id": sid, "lv": 1, "kv": 1, "tv": -1, "yv": 1}, headers=head)
        data = (lr.json() if lr.status_code == 200 else {}) or {}
        text = (data.get("lrc") or {}).get("lyric") or ""
        instrumental = "纯音乐" in text
        # r41b: «yrc» — NetEase word timing with the duration of every line and word (where the singing ends)
        synced = None if instrumental else (_strip_credits(parse_yrc((data.get("yrc") or {}).get("lyric")), cand["title"])
                                            or _strip_credits(parse_lrc(text), cand["title"]))
        c = _cand("netease", cand["title"], cand["artist"], cand["duration"], synced, None, instrumental)
        if c:
            out.append(c)
    return out


def _qq(client, q) -> List[Dict[str, Any]]:
    head = {"Referer": "https://y.qq.com/portal/player.html"}
    r = client.get("https://c.y.qq.com/soso/fcgi-bin/client_search_cp",
                   params={"w": f"{q['artist']} {q['title']}".strip(), "format": "json", "p": 1, "n": 8, "cr": 1}, headers=head)
    songs = ((((r.json() if r.status_code == 200 else {}) or {}).get("data") or {}).get("song") or {}).get("list") or []
    out = []
    for s in songs:
        cand = {"title": s.get("songname") or "", "artist": ((s.get("singer") or [{}])[0] or {}).get("name") or "",
                "duration": s.get("interval") or 0}
        if not s.get("songmid") or score(cand, q) <= 0:
            continue
        lr = client.get("https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg",
                        params={"songmid": s["songmid"], "format": "json", "nobase64": 1, "g_tk": 5381}, headers=head)
        text = html.unescape(((lr.json() if lr.status_code == 200 else {}) or {}).get("lyric") or "")
        c = _cand("qq", cand["title"], cand["artist"], cand["duration"], _strip_credits(parse_lrc(text), cand["title"]), None)
        if c:
            out.append(c)
        if len(out) >= 2:
            break
    return out


def genius_lyrics(page: str) -> str:
    """Text of the ``data-lyrics-container`` blocks of a genius.com song page (``<br>`` → new line, no tags)."""
    out = []
    for m in re.finditer(r'<div[^>]*data-lyrics-container="true"[^>]*>', page):
        depth, i = 1, m.end()
        for tag in re.finditer(r"<(/?)div\b[^>]*>", page[i:]):
            depth += -1 if tag.group(1) else 1
            if depth == 0:
                out.append(page[i:i + tag.start()])
                break
    text = "\n".join(out)
    text = re.sub(r'<div[^>]*data-exclude-from-selection="true"[^>]*>.*?</div>', "", text, flags=re.S)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(l.strip() for l in text.splitlines())).strip()


def _genius(client, q) -> List[Dict[str, Any]]:
    r = client.get("https://genius.com/api/search/multi", params={"per_page": 5, "q": f"{q['artist']} {q['title']}".strip()},
                   headers={"User-Agent": _UA})
    if r.status_code != 200:
        raise RuntimeError(f"Genius {r.status_code}")
    hits = []
    for section in ((r.json() or {}).get("response") or {}).get("sections") or []:
        if section.get("type") in ("song", "top_hit"):
            hits += [h.get("result") or {} for h in section.get("hits") or [] if h.get("type") == "song"]
    for h in hits:
        artist = (h.get("primary_artist") or {}).get("name") or h.get("artist_names") or ""
        if not h.get("url") or score({"title": h.get("title") or "", "artist": artist}, q) <= 0:
            continue
        page = client.get(h["url"], headers={"User-Agent": _UA})
        c = _cand("genius", h.get("title") or "", artist, 0, None, genius_lyrics(page.text) if page.status_code == 200 else "")
        return [c] if c else []
    return []


def _lyricsovh(client, q) -> List[Dict[str, Any]]:
    if not q["artist"]:
        return []
    from urllib.parse import quote
    r = client.get(f"https://api.lyrics.ovh/v1/{quote(q['artist'], safe='')}/{quote(q['title'], safe='')}")
    text = ((r.json() if r.status_code == 200 else {}) or {}).get("lyrics") or ""
    text = re.sub(r"^Paroles de la chanson .*?\n", "", text.replace("\r\n", "\n"))
    c = _cand("lyricsovh", q["title"], q["artist"], 0, None, re.sub(r"\n{3,}", "\n\n", text))
    return [c] if c else []


PROVIDERS: Dict[str, Callable[[Any, Dict[str, Any]], List[Dict[str, Any]]]] = {
    "lrclib": _lrclib, "musixmatch": _musixmatch, "netease": _netease, "qq": _qq, "genius": _genius, "lyricsovh": _lyricsovh}


def _default_client():
    import httpx
    return httpx.Client(timeout=httpx.Timeout(6, read=8), follow_redirects=True,
                        headers={"User-Agent": "MemuAI MemuMusic (local music player)"})


def search(q: Dict[str, Any], sources: List[str], client_factory: Optional[Callable[[], Any]] = None,
           plain_fallback: bool = True) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """Ask every source in parallel; returns (ranked candidates, {source: "ok" | "empty" | error}).
    Plain-only sources are asked together with the synced ones (no extra wait) but count only as a fallback."""
    factory = client_factory or _default_client
    sources = [s for s in sources if s in PROVIDERS]
    status: Dict[str, str] = {}
    if not sources:
        return [], status

    def run(name: str) -> List[Dict[str, Any]]:
        with factory() as client:
            return PROVIDERS[name](client, q)

    pool = ThreadPoolExecutor(max_workers=len(sources), thread_name_prefix="memumusic-lyrics")
    futures = {pool.submit(run, name): name for name in sources}
    done, _ = wait(futures, timeout=DEADLINE)
    pool.shutdown(wait=False, cancel_futures=True)
    cands: List[Dict[str, Any]] = []
    for fut, name in futures.items():
        if fut not in done:
            status[name] = "timeout"
            continue
        try:
            found = fut.result()
        except Exception as error:   # one broken source never breaks the search
            status[name] = str(error)[:120] or type(error).__name__
            continue
        status[name] = "ok" if found else "empty"
        cands += found
    order = {name: i for i, name in enumerate(sources)}
    for c in cands:
        c["score"] = score(c, q)
        if c["source"] == "lyricsovh" and not c["score"]:
            c["score"] = 0.55   # a direct «artist/title» lookup: the title is ours by construction
    cands = [c for c in cands if c["score"] > 0.55 or c.get("instrumental")]
    has_synced = any(c["synced"] for c in cands)
    if has_synced or not plain_fallback:
        cands = [c for c in cands if c["synced"] or c.get("instrumental")] + \
                ([c for c in cands if not c["synced"] and not c.get("instrumental")] if plain_fallback else [])
    # r41-ui: timings are only right for a version of the same length — among the synced ones the duration match (±2 s)
    # goes first, an unknown duration next, another version (a mismatch) last, whatever its score.
    def fit(c: Dict[str, Any]) -> int:
        dur, want = float(c.get("duration") or 0), q["duration"]
        return 1 if not (dur and want) else 0 if abs(dur - want) <= 2 else 2
    cands.sort(key=lambda c: (not c["synced"], fit(c) if c["synced"] else 0, -c["score"], order.get(c["source"], 99)))
    # the same text uploaded many times (LRCLIB) is one variant; at most 3 variants per source
    out, seen, per = [], set(), {}
    for c in cands:
        key = (c["source"], bool(c["synced"]), norm(" ".join((c["plain"] or "").splitlines()[:4]))[:160])
        if key in seen or per.get(c["source"], 0) >= 3:
            continue
        seen.add(key)
        per[c["source"]] = per.get(c["source"], 0) + 1
        out.append(c)
    return out[:MAX_ALTS], status


# ---------------------------------------------------------------------- storage (own table, next to «lyrics»)

def _ensure(lib: Library) -> None:
    if getattr(lib, "_lyrics_alts_ready", False):
        return
    lib._x("CREATE TABLE IF NOT EXISTS lyrics_alts (track_id TEXT PRIMARY KEY, alts TEXT NOT NULL DEFAULT '[]', "
           "choice INTEGER NOT NULL DEFAULT 0, offset REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT '{}', "
           "fetched_at REAL NOT NULL DEFAULT 0)")
    lib._lyrics_alts_ready = True


def _meta(lib: Library, track_id: str) -> Dict[str, Any]:
    _ensure(lib)
    rows = lib._q("SELECT * FROM lyrics_alts WHERE track_id=?", (track_id,))
    if not rows:
        return {"alts": [], "choice": 0, "offset": 0.0, "status": {}}
    r = rows[0]
    return {"alts": json.loads(r["alts"] or "[]"), "choice": int(r["choice"]), "offset": float(r["offset"]),
            "status": json.loads(r["status"] or "{}")}


def _save_meta(lib: Library, track_id: str, **fields: Any) -> None:
    cur = {**_meta(lib, track_id), **fields}
    lib._x("INSERT OR REPLACE INTO lyrics_alts (track_id, alts, choice, offset, status, fetched_at) VALUES (?,?,?,?,?,?)",
           (track_id, json.dumps(cur["alts"], ensure_ascii=False), int(cur["choice"]), float(cur["offset"]),
            json.dumps(cur["status"]), time.time()))


def _summary(alts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{"index": i, "source": a["source"], "name": SOURCE_NAMES.get(a["source"], a["source"]),
             "synced": bool(a.get("synced")), "words": bool(a.get("synced") and any(len(r) > 2 and r[2] for r in a["synced"])),
             "duration": a.get("duration") or 0, "title": a.get("title") or "", "artist": a.get("artist") or "",
             "lines": len(a["synced"]) if a.get("synced") else len([l for l in (a.get("plain") or "").splitlines() if l.strip()]),
             "instrumental": bool(a.get("instrumental"))} for i, a in enumerate(alts)]


def _answer(lib: Library, track_id: str, cached: bool) -> Dict[str, Any]:
    row = lib.cached_lyrics(track_id) or {"synced": None, "plain": None, "source": ""}
    meta = _meta(lib, track_id)
    chosen = meta["alts"][meta["choice"]] if 0 <= meta["choice"] < len(meta["alts"]) else {}
    synced = row["synced"]
    # r41-ui: the shown timings were made for a version of another length → the window warns («Other version · ±N s»)
    track = lib.row(track_id)
    want, got = float((track["duration"] if track else 0) or 0), float(chosen.get("duration") or 0)
    diff = round(abs(got - want), 1) if synced and want and got and row["source"] == chosen.get("source") else 0.0
    try:
        general = max(-2.0, min(2.0, float(lib.settings().get("lyrics_offset_ms") or 0) / 1000))
    except (TypeError, ValueError):
        general = 0.0
    return {"synced": synced, "plain": row["plain"], "source": row["source"], "duration_diff": diff, "general_offset": general,
            "source_name": SOURCE_NAMES.get(row["source"], row["source"]),
            "instrumental": bool(chosen.get("instrumental")) and row["source"] == chosen.get("source"),
            "words": bool(synced and any(len(r) > 2 and r[2] for r in synced)), "offset": meta["offset"],
            "choice": meta["choice"], "alts": _summary(meta["alts"]), "status": meta["status"], "cached": cached}


def _apply(lib: Library, track_id: str, cand: Optional[Dict[str, Any]]) -> None:
    lib.store_lyrics(track_id, cand["synced"] if cand else None, cand["plain"] if cand else None,
                     cand["source"] if cand else "")


def fetch(lib: Library, track_id: str, client_factory: Optional[Callable[[], Any]] = None,
          force: bool = False) -> Dict[str, Any]:
    row = lib.row(track_id)
    if not row:
        raise KeyError("Трек не найден / Track not found")
    cached = lib.cached_lyrics(track_id)
    if cached and not force and (cached["synced"] or cached["plain"] or time.time() - cached["fetched_at"] < MISS_TTL):
        return _answer(lib, track_id, True)
    settings = lib.settings()
    if not force and not cached and not settings.get("auto_lyrics", True):
        return {**_answer(lib, track_id, True), "auto_off": True}
    q = query_of(dict(row))
    chosen = settings.get("lyrics_sources")
    started = time.time()
    cands, status = search(q, list(chosen) if isinstance(chosen, list) else list(SOURCES), client_factory,
                           bool(settings.get("lyrics_plain_fallback", True)))
    if not cands and status and all(v not in ("ok", "empty") for v in status.values()):
        raise RuntimeError("Источники текста недоступны / Lyrics sources unavailable")
    _save_meta(lib, track_id, alts=cands, choice=0, status=status)
    # r40 (r40-music-fix): the search takes seconds (the background prefetch, «Find»); lyrics the user saved meanwhile win —
    # they used to be overwritten by the late search result («my text did not save»).
    mine = lib.cached_lyrics(track_id)
    if not (mine and mine["source"] == "manual" and mine["fetched_at"] >= started):
        _apply(lib, track_id, cands[0] if cands else None)
    return _answer(lib, track_id, False)


def choose(lib: Library, track_id: str, index: Optional[int] = None, step: int = 0) -> Dict[str, Any]:
    """``lyrics.choose``: show another found variant (``index``) or the next one (``next``, wraps around)."""
    if not lib.row(track_id):
        raise KeyError("Трек не найден / Track not found")
    meta = _meta(lib, track_id)
    if not meta["alts"]:
        return _answer(lib, track_id, True)
    n = len(meta["alts"])
    pick = (meta["choice"] + step) % n if index is None else max(0, min(n - 1, int(index)))
    _save_meta(lib, track_id, choice=pick)
    _apply(lib, track_id, meta["alts"][pick])
    return _answer(lib, track_id, True)


def set_offset(lib: Library, track_id: str, offset: float) -> Dict[str, Any]:
    """``lyrics.offset``: per-track shift in seconds (+ = lyrics later), kept with the track."""
    if not lib.row(track_id):
        raise KeyError("Трек не найден / Track not found")
    value = round(max(-30.0, min(30.0, float(offset or 0))), 2)
    _save_meta(lib, track_id, offset=value)
    return {"offset": value}


def set_manual(lib: Library, track_id: str, text: str) -> Dict[str, Any]:
    """``lyrics.set``: lyrics the user pasted (plain text or LRC). Empty text forgets them (the next get searches again)."""
    if not lib.row(track_id):
        raise KeyError("Трек не найден / Track not found")
    text = (text or "").replace("\r\n", "\n").strip()[:200_000]
    if not text:
        lib.forget_lyrics(track_id)
        return {"synced": None, "plain": None, "source": ""}
    synced = parse_lrc(text)
    plain = "\n".join(row[1] for row in synced) if synced else text
    lib.store_lyrics(track_id, synced, plain, "manual")
    # r41-ui: the whole answer (the track's offset and the found variants stay) — the window used to reset the offset to 0
    return _answer(lib, track_id, True)


# ---------------------------------------------------------------------- background prefetch (r37 wave 2)

class Prefetcher:
    """Fetches lyrics for newly added tracks in the background: one track at a time with a pause between
    them (the sources are free community services), a daemon thread that exits when the queue is empty.
    Already cached tracks are skipped; failures are silent (``lyrics.get`` retries on demand)."""

    PAUSE = 2.5

    def __init__(self, library_factory: Optional[Callable[[], Library]] = None,
                 client_factory: Optional[Callable[[], Any]] = None, pause: Optional[float] = None):
        import collections
        import threading
        self.library_factory = library_factory
        self.client_factory = client_factory
        self.pause = self.PAUSE if pause is None else pause
        self.queue: "collections.deque[str]" = collections.deque(maxlen=2000)
        self.lock = threading.Lock()
        self.thread: Optional[threading.Thread] = None
        self.done = 0
        self.failed = 0

    def _lib(self) -> Library:
        if self.library_factory:
            return self.library_factory()
        from .library import get_library
        return get_library()

    def add(self, track_id: str) -> bool:
        import threading
        track_id = str(track_id or "")
        if not track_id:
            return False
        try:
            if not self._lib().settings().get("auto_lyrics", True):
                return False
        except Exception:
            return False
        with self.lock:
            if track_id in self.queue:
                return False
            self.queue.append(track_id)
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._loop, name="memumusic-lyrics", daemon=True)
                self.thread.start()
        return True

    def status(self) -> Dict[str, Any]:
        with self.lock:
            return {"queued": len(self.queue), "done": self.done, "failed": self.failed,
                    "running": bool(self.thread and self.thread.is_alive())}

    def _loop(self) -> None:
        first = True
        while True:
            with self.lock:
                if not self.queue:
                    self.thread = None
                    return
                track_id = self.queue.popleft()
            try:
                lib = self._lib()
                cached = lib.cached_lyrics(track_id)
                if cached is not None or not lib.row(track_id):
                    continue
                if not first and self.pause:
                    time.sleep(self.pause)
                first = False
                fetch(lib, track_id, self.client_factory)
                self.done += 1
            except Exception:
                self.failed += 1


_PREFETCHER: Optional[Prefetcher] = None


def prefetcher() -> Prefetcher:
    global _PREFETCHER
    if _PREFETCHER is None:
        _PREFETCHER = Prefetcher()
    return _PREFETCHER
