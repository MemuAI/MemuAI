# -*- coding: utf-8 -*-
"""Strict source detection by link + metadata for services yt-dlp cannot download.

``detect(url)`` never touches the network. ``resolve_meta(info)`` reads PUBLIC pages/APIs only:
Spotify embed page (``__NEXT_DATA__``), iTunes lookup API, Deezer public API, Yandex Music API,
schema.org JSON-LD and ``og:*`` tags as a generic fallback (Tidal, Amazon Music, Apple playlists…).
song.link/Odesli closed its public API in 2026 (401 PUBLIC_API_ACCESS_DEPRECATED), so it is not used.
The resolved items are then matched on YouTube Music by :mod:`app.music.downloader`.
"""
from __future__ import annotations

import html
import json
import re
import urllib.parse
from typing import Any, Dict, List, Optional

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36")
#: Sources yt-dlp downloads itself (anything else with http(s) is tried through yt-dlp too).
DIRECT = {"youtube", "ytmusic", "soundcloud", "bandcamp", "vk", "vimeo", "mixcloud", "audiomack", "tiktok",
          "twitch", "dailymotion", "rutube", "odnoklassniki", "web"}
#: Sources resolved through metadata + a YouTube Music search.
META = {"spotify", "apple", "deezer", "yandex", "tidal", "amazon", "napster", "qobuz", "pandora", "boomplay", "anghami", "zvuk"}

LABELS = {"youtube": "YouTube", "ytmusic": "YouTube Music", "spotify": "Spotify", "apple": "Apple Music",
          "soundcloud": "SoundCloud", "deezer": "Deezer", "yandex": "Яндекс Музыка", "vk": "VK", "bandcamp": "Bandcamp",
          "tidal": "TIDAL", "amazon": "Amazon Music", "vimeo": "Vimeo", "mixcloud": "Mixcloud", "audiomack": "Audiomack",
          "tiktok": "TikTok", "twitch": "Twitch", "dailymotion": "Dailymotion", "rutube": "RUTUBE",
          "odnoklassniki": "OK", "napster": "Napster", "qobuz": "Qobuz", "pandora": "Pandora", "boomplay": "Boomplay",
          "anghami": "Anghami", "zvuk": "Звук", "web": "Web", "search": "Search"}


def _result(source: str, kind: str, url: str, item_id: str = "", **extra: Any) -> Dict[str, Any]:
    return {"source": source, "kind": kind, "url": url, "id": item_id, "label": LABELS.get(source, source),
            "direct": source in DIRECT, **extra}


def detect(raw: str) -> Dict[str, Any]:
    text = str(raw or "").strip()
    m = re.match(r"^spotify:(track|album|playlist|artist|episode):([A-Za-z0-9]+)$", text)
    if m:
        return _result("spotify", "track" if m.group(1) == "episode" else m.group(1),
                       f"https://open.spotify.com/{m.group(1)}/{m.group(2)}", m.group(2))
    if not re.match(r"^https?://", text, re.I):
        if re.match(r"^[\w.-]+\.[a-z]{2,}/", text, re.I):
            text = "https://" + text
        else:
            return _result("search", "query", text) if text else _result("", "", "")
    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError:
        return _result("", "", text)
    host = (parts.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    path = parts.path or "/"
    query = urllib.parse.parse_qs(parts.query)

    def has(*domains: str) -> bool:
        return any(host == d or host.endswith("." + d) for d in domains)

    if host == "music.youtube.com":
        if path.startswith("/watch") and query.get("v"):
            return _result("ytmusic", "track", text, query["v"][0])
        if path.startswith("/playlist") and query.get("list"):
            lid = query["list"][0]
            return _result("ytmusic", "album" if lid.startswith("OLAK5uy") else "playlist", text, lid)
        if path.startswith("/browse/MPREb"):
            return _result("ytmusic", "album", text, path.rsplit("/", 1)[-1])
        if path.startswith("/channel/") or path.startswith("/browse/UC"):
            return _result("ytmusic", "artist", text, path.rsplit("/", 1)[-1])
        return _result("ytmusic", "track", text)
    if has("youtube.com", "youtube-nocookie.com") or host == "youtu.be":
        if host == "youtu.be":
            return _result("youtube", "track", text, path.strip("/").split("/")[0])
        if path.startswith("/watch") and query.get("v"):
            return _result("youtube", "track", text, query["v"][0])
        if path.startswith(("/shorts/", "/live/", "/embed/")):
            return _result("youtube", "track", text, path.split("/")[2] if len(path.split("/")) > 2 else "")
        if path.startswith("/playlist") and query.get("list"):
            return _result("youtube", "playlist", text, query["list"][0])
        if path.startswith(("/@", "/channel/", "/c/", "/user/")):
            return _result("youtube", "artist", text, path.strip("/").split("/")[0 if path.startswith("/@") else 1])
        return _result("youtube", "track", text)
    if host == "open.spotify.com" or host == "play.spotify.com":
        m = re.search(r"/(track|album|playlist|artist|episode)/([A-Za-z0-9]+)", path)
        if m:
            kind = "track" if m.group(1) == "episode" else m.group(1)
            return _result("spotify", kind, text, m.group(2))
        return _result("spotify", "track", text)
    if host in ("spotify.link", "spoti.fi"):
        return _result("spotify", "track", text, short=True)
    if has("music.apple.com", "itunes.apple.com", "geo.music.apple.com"):
        m = re.search(r"/(album|song|playlist|artist|music-video)/(?:[^/]+/)?((?:pl\.)?[\w.-]+)$", path.rstrip("/"))
        if query.get("i"):
            return _result("apple", "track", text, query["i"][0], country=_apple_country(path))
        if m:
            kind = {"song": "track", "music-video": "track"}.get(m.group(1), m.group(1))
            return _result("apple", kind, text, m.group(2), country=_apple_country(path))
        return _result("apple", "track", text)
    if has("soundcloud.com"):
        if host == "on.soundcloud.com":
            return _result("soundcloud", "track", text, short=True)
        segs = [s for s in path.split("/") if s]
        if len(segs) >= 3 and segs[1] == "sets":
            return _result("soundcloud", "playlist", text, "/".join(segs[:3]))
        if len(segs) == 1 or (len(segs) == 2 and segs[1] in ("tracks", "albums", "sets", "popular-tracks", "reposts")):
            return _result("soundcloud", "artist", text, segs[0])
        return _result("soundcloud", "track", text, "/".join(segs[:2]))
    if has("deezer.com", "deezer.page.link") or host == "link.deezer.com" or host == "dzr.page.link":
        m = re.search(r"/(track|album|playlist|artist)/(\d+)", path)
        if m:
            return _result("deezer", m.group(1), text, m.group(2))
        return _result("deezer", "track", text, short=True)
    if re.match(r"^music\.yandex\.(ru|com|by|kz|uz|com\.tr)$", host) or host == "music.ya.ru":
        m = re.search(r"/album/(\d+)/track/(\d+)", path)
        if m:
            return _result("yandex", "track", text, m.group(2), album=m.group(1))
        m = re.search(r"/track/(\d+)", path)
        if m:
            return _result("yandex", "track", text, m.group(1))
        m = re.search(r"/album/(\d+)", path)
        if m:
            return _result("yandex", "album", text, m.group(1))
        m = re.search(r"/users/([^/]+)/playlists/(\d+)", path)
        if m:
            return _result("yandex", "playlist", text, f"{m.group(1)}:{m.group(2)}")
        m = re.search(r"/playlists/([\w-]+)", path)
        if m:
            return _result("yandex", "playlist", text, m.group(1))
        m = re.search(r"/artist/(\d+)", path)
        if m:
            return _result("yandex", "artist", text, m.group(1))
        return _result("yandex", "track", text)
    if has("tidal.com"):
        m = re.search(r"/(track|album|playlist|artist|video)/([\w-]+)", path)
        if m:
            return _result("tidal", "track" if m.group(1) == "video" else m.group(1), text, m.group(2))
        return _result("tidal", "track", text)
    if host.startswith("music.amazon.") or (host.startswith("amazon.") and "/music/" in path):
        if query.get("trackAsin"):
            return _result("amazon", "track", text, query["trackAsin"][0])
        m = re.search(r"/(albums|tracks|playlists|user-playlists|artists)/([\w-]+)", path)
        if m:
            kind = {"albums": "album", "tracks": "track", "playlists": "playlist", "user-playlists": "playlist",
                    "artists": "artist"}[m.group(1)]
            return _result("amazon", kind, text, m.group(2))
        return _result("amazon", "track", text)
    if has("bandcamp.com"):
        if "/album/" in path:
            return _result("bandcamp", "album", text)
        if "/track/" in path:
            return _result("bandcamp", "track", text)
        return _result("bandcamp", "artist", text)
    if has("vk.com", "vk.ru", "vkvideo.ru"):
        kind = "playlist" if re.search(r"(playlist|album)", path + parts.query) else "track"
        return _result("vk", kind, text)
    for domain, source in (("vimeo.com", "vimeo"), ("mixcloud.com", "mixcloud"), ("audiomack.com", "audiomack"),
                           ("tiktok.com", "tiktok"), ("twitch.tv", "twitch"), ("dailymotion.com", "dailymotion"),
                           ("rutube.ru", "rutube"), ("ok.ru", "odnoklassniki"), ("napster.com", "napster"),
                           ("qobuz.com", "qobuz"), ("pandora.com", "pandora"), ("boomplay.com", "boomplay"),
                           ("anghami.com", "anghami"), ("zvuk.com", "zvuk")):
        if has(domain):
            kind = "playlist" if re.search(r"/(playlist|album|sets)s?/", path) else "track"
            if source == "mixcloud" and len([s for s in path.split("/") if s]) == 1:
                kind = "artist"
            return _result(source, kind, text)
    return _result("web", "track", text)


def _apple_country(path: str) -> str:
    m = re.match(r"^/([a-z]{2})/", path)
    return m.group(1) if m else "us"


# ---------------------------------------------------------------------- metadata

def _client():
    import httpx
    return httpx.Client(follow_redirects=True, timeout=httpx.Timeout(12, read=20),
                        headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8,ru;q=0.6"})


def _item(title: str, artist: str = "", album: str = "", duration: float = 0, cover: str = "",
          url: str = "", isrc: str = "", track_no: int = 0, year: str = "") -> Dict[str, Any]:
    return {"title": str(title or "").strip(), "artist": str(artist or "").strip(), "album": str(album or "").strip(),
            "duration": float(duration or 0), "cover_url": cover or "", "source_url": url, "isrc": isrc,
            "track_no": int(track_no or 0), "year": str(year or "")[:4]}


def expand_short(info: Dict[str, Any], client) -> Dict[str, Any]:
    """spotify.link / link.deezer.com / on.soundcloud.com → the real URL, then detect again."""
    if not info.get("short"):
        return info
    response = client.get(info["url"])
    final = str(response.url)
    if final == info["url"]:
        m = re.search(r'https://(?:open\.spotify\.com|www\.deezer\.com|soundcloud\.com)/[^"\'<>\s]+', response.text)
        final = html.unescape(m.group(0)) if m else final
    fresh = detect(final)
    fresh.pop("short", None)
    return fresh


def resolve_meta(info: Dict[str, Any], client=None) -> Dict[str, Any]:
    """{kind, title, artist, cover_url, items:[item]} for a META source (items are what we search for)."""
    own = client is None
    client = client or _client()
    try:
        info = expand_short(info, client)
        source = info["source"]
        handler = {"spotify": _spotify, "apple": _apple, "deezer": _deezer, "yandex": _yandex}.get(source, _generic)
        try:
            meta = handler(info, client)
        except Exception:
            if handler is _generic:
                raise
            meta = _generic(info, client)
        meta.setdefault("kind", info["kind"])
        meta.setdefault("source", source)
        for item in meta.get("items", []):
            if not item.get("cover_url"):
                item["cover_url"] = meta.get("cover_url", "")
            if not item.get("album") and meta.get("kind") == "album":
                item["album"] = meta.get("title", "")
        return meta
    finally:
        if own:
            client.close()


def _next_data(text: str) -> Dict[str, Any]:
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', text, re.S)
    if not m:
        raise ValueError("no __NEXT_DATA__")
    return json.loads(m.group(1))


def _spotify(info: Dict[str, Any], client) -> Dict[str, Any]:
    kind, sid = info["kind"], info["id"]
    response = client.get(f"https://open.spotify.com/embed/{kind}/{sid}")
    response.raise_for_status()
    entity = _next_data(response.text)["props"]["pageProps"]["state"]["data"]["entity"]
    images = sorted((entity.get("visualIdentity") or {}).get("image") or entity.get("coverArt", {}).get("sources") or [],
                    key=lambda i: -(i.get("maxWidth") or i.get("width") or 0))
    cover = images[0]["url"] if images else ""
    if kind == "track":
        artist = ", ".join(a.get("name", "") for a in entity.get("artists") or []) or entity.get("subtitle", "")
        title = entity.get("title") or entity.get("name") or ""
        return {"kind": "track", "title": title, "artist": artist, "cover_url": cover,
                "items": [_item(title, artist, duration=(entity.get("duration") or 0) / 1000, cover=cover, url=info["url"])]}
    items = [_item(t.get("title", ""), t.get("subtitle", ""), duration=(t.get("duration") or 0) / 1000,
                   url="https://open.spotify.com/track/" + str(t.get("uri", "")).rsplit(":", 1)[-1], track_no=i + 1)
             for i, t in enumerate(entity.get("trackList") or []) if t.get("title")]
    if kind == "album":
        for it in items:
            it["album"] = entity.get("title") or entity.get("name") or ""
    else:
        for it in items:
            it["track_no"] = 0
    return {"kind": kind, "title": entity.get("title") or entity.get("name") or "", "artist": entity.get("subtitle", ""),
            "cover_url": cover, "items": items}


def _art(url: str, size: int = 1000) -> str:
    return re.sub(r"/\d+x\d+bb\.(jpg|png)$", f"/{size}x{size}bb.jpg", url or "")


def _apple(info: Dict[str, Any], client) -> Dict[str, Any]:
    kind, aid, country = info["kind"], info["id"], info.get("country", "us")
    if kind in ("track", "album") and aid.isdigit():
        params = {"id": aid, "country": country}
        if kind == "album":
            params.update(entity="song", limit="200")
        data = client.get("https://itunes.apple.com/lookup", params=params).json()
        results = data.get("results") or []
        songs = [r for r in results if r.get("wrapperType") == "track"]
        head = next((r for r in results if r.get("wrapperType") == "collection"), songs[0] if songs else {})
        items = [_item(s.get("trackName", ""), s.get("artistName", ""), s.get("collectionName", ""),
                       (s.get("trackTimeMillis") or 0) / 1000, _art(s.get("artworkUrl100", "")),
                       s.get("trackViewUrl", ""), track_no=s.get("trackNumber") or 0,
                       year=str(s.get("releaseDate", ""))[:4]) for s in songs]
        if not items:
            raise ValueError("itunes: nothing")
        title = head.get("collectionName") if kind == "album" else items[0]["title"]
        return {"kind": kind, "title": title or "", "artist": head.get("artistName", ""),
                "cover_url": _art(head.get("artworkUrl100", "")), "items": items}
    return _generic(info, client)


def _deezer(info: Dict[str, Any], client) -> Dict[str, Any]:
    kind, did = info["kind"], info["id"]
    data = client.get(f"https://api.deezer.com/{kind}/{did}").json()
    if data.get("error"):
        raise ValueError(str(data["error"]))
    if kind == "track":
        album = data.get("album") or {}
        it = _item(data.get("title", ""), (data.get("artist") or {}).get("name", ""), album.get("title", ""),
                   data.get("duration") or 0, album.get("cover_xl") or album.get("cover_big", ""), data.get("link", ""),
                   data.get("isrc", ""), data.get("track_position") or 0, str(data.get("release_date", ""))[:4])
        return {"kind": "track", "title": it["title"], "artist": it["artist"], "cover_url": it["cover_url"], "items": [it]}
    rows = list((data.get("tracks") or {}).get("data") or [])
    nxt = (data.get("tracks") or {}).get("next")
    while nxt and len(rows) < 1000:
        page = client.get(nxt).json()
        rows += page.get("data") or []
        nxt = page.get("next")
    cover = data.get("cover_xl") or data.get("picture_xl") or ""
    items = [_item(t.get("title", ""), (t.get("artist") or {}).get("name", ""),
                   (t.get("album") or {}).get("title", data.get("title", "") if kind == "album" else ""),
                   t.get("duration") or 0, (t.get("album") or {}).get("cover_xl", "") or cover, t.get("link", ""),
                   track_no=(i + 1) if kind == "album" else 0) for i, t in enumerate(rows)]
    return {"kind": kind, "title": data.get("title") or data.get("name") or "",
            "artist": (data.get("artist") or {}).get("name", "") or (data.get("creator") or {}).get("name", ""),
            "cover_url": cover, "items": items}


def _ya_cover(uri: str) -> str:
    return ("https://" + uri.replace("%%", "800x800")) if uri else ""


def _ya_item(t: Dict[str, Any]) -> Dict[str, Any]:
    albums = t.get("albums") or [{}]
    return _item(t.get("title", ""), ", ".join(a.get("name", "") for a in t.get("artists") or []),
                 albums[0].get("title", ""), (t.get("durationMs") or 0) / 1000, _ya_cover(t.get("coverUri", "")),
                 f"https://music.yandex.ru/track/{t.get('id', '')}", year=str(albums[0].get("year", "")))


def _yandex(info: Dict[str, Any], client) -> Dict[str, Any]:
    api = "https://api.music.yandex.net"
    kind, yid = info["kind"], info["id"]
    if kind == "track":
        data = client.get(f"{api}/tracks/{yid}").json()
        tracks = data.get("result") or []
        if not tracks:
            raise ValueError("yandex: " + json.dumps(data.get("error"))[:120])
        it = _ya_item(tracks[0])
        return {"kind": "track", "title": it["title"], "artist": it["artist"], "cover_url": it["cover_url"], "items": [it]}
    if kind == "album":
        res = client.get(f"{api}/albums/{yid}/with-tracks").json().get("result")
        if not res:
            raise ValueError("yandex album")
        items = [_ya_item(t) for vol in res.get("volumes") or [] for t in vol]
        for i, it in enumerate(items):
            it["track_no"] = i + 1
        return {"kind": "album", "title": res.get("title", ""), "artist": ", ".join(a.get("name", "") for a in res.get("artists") or []),
                "cover_url": _ya_cover(res.get("coverUri", "")), "items": items}
    if kind == "playlist" and ":" in yid:
        user, num = yid.split(":", 1)
        res = client.get(f"{api}/users/{user}/playlists/{num}").json().get("result")
        if not res:
            raise ValueError("yandex playlist")
        items = [_ya_item(t.get("track") or t) for t in res.get("tracks") or [] if (t.get("track") or t).get("title")]
        cover = (res.get("cover") or {}).get("uri") or res.get("ogImage", "")
        return {"kind": "playlist", "title": res.get("title", ""), "artist": (res.get("owner") or {}).get("name", ""),
                "cover_url": _ya_cover(cover), "items": items}
    raise ValueError("yandex: unsupported")


def _meta_tag(text: str, name: str) -> str:
    for pattern in (rf'<meta[^>]+(?:property|name)=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']*)["\']',
                    rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']{re.escape(name)}["\']'):
        m = re.search(pattern, text, re.I)
        if m:
            return html.unescape(m.group(1)).strip()
    return ""


def _jsonld(text: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', text, re.S | re.I):
        try:
            data = json.loads(raw.strip())
        except ValueError:
            continue
        for node in (data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []):
            if isinstance(node, dict):
                out.append(node)
    return out


def _name(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(_name(v) for v in value if _name(v))
    if isinstance(value, dict):
        return str(value.get("name") or "")
    return str(value or "")


def _iso_duration(value: Any) -> float:
    m = re.match(r"^P(?:T)?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?$", str(value or ""))
    if not m:
        return 0.0
    h, mi, s = m.groups()
    return int(h or 0) * 3600 + int(mi or 0) * 60 + float(s or 0)


def _generic(info: Dict[str, Any], client) -> Dict[str, Any]:
    """JSON-LD (MusicRecording / MusicAlbum / MusicPlaylist), else og:title / og:description."""
    response = client.get(info["url"])
    response.raise_for_status()
    text = response.text
    cover = _meta_tag(text, "og:image")
    for node in _jsonld(text):
        types = node.get("@type")
        types = types if isinstance(types, list) else [types]
        if "MusicRecording" in types:
            it = _item(node.get("name", ""), _name(node.get("byArtist")), _name(node.get("inAlbum")),
                       _iso_duration(node.get("duration")), _name(node.get("image")) or cover, info["url"])
            return {"kind": "track", "title": it["title"], "artist": it["artist"], "cover_url": it["cover_url"], "items": [it]}
        if "MusicAlbum" in types or "MusicPlaylist" in types:
            tracks = node.get("track") or node.get("tracks") or []
            if isinstance(tracks, dict):
                tracks = tracks.get("itemListElement") or [tracks]
            album_artist = _name(node.get("byArtist"))
            items = []
            for i, t in enumerate(tracks):
                t = t.get("item", t) if isinstance(t, dict) else {}
                if t.get("name"):
                    items.append(_item(t["name"], _name(t.get("byArtist")) or album_artist,
                                       node.get("name", "") if "MusicAlbum" in types else _name(t.get("inAlbum")),
                                       _iso_duration(t.get("duration")), "", t.get("url", ""),
                                       track_no=(i + 1) if "MusicAlbum" in types else 0))
            if items:
                return {"kind": "album" if "MusicAlbum" in types else "playlist", "title": node.get("name", ""),
                        "artist": album_artist, "cover_url": _name(node.get("image")) or cover, "items": items}
    title = _meta_tag(text, "og:title") or _meta_tag(text, "twitter:title")
    if not title:
        m = re.search(r"<title>(.*?)</title>", text, re.S | re.I)
        title = html.unescape(m.group(1)).strip() if m else ""
    desc = _meta_tag(text, "og:description")
    artist = _meta_tag(text, "music:musician_description") or _artist_from_desc(desc)
    title = re.sub(r"\s*[|\-–—]\s*(Spotify|Apple Music|TIDAL|Amazon Music|Deezer|Яндекс Музыка|Yandex Music).*$", "", title)
    m = re.match(r"^(.*)\s+by\s+(.+)$", title) or re.match(r"^(.*?)\s+(?:—|-|–)\s+(.+)$", title)
    if not artist and m:
        title, artist = m.group(1).strip(), m.group(2).strip()
    if not title:
        raise ValueError("Не удалось прочитать страницу / Could not read the page")
    return {"kind": info["kind"], "title": title, "artist": artist, "cover_url": cover,
            "items": [_item(title, artist, cover=cover, url=info["url"])], "approximate": info["kind"] != "track"}


def _artist_from_desc(desc: str) -> str:
    m = re.match(r"^(?:Listen to .*? by |Song · |Album · |Single · )?([^·]+?)\s*·", desc or "")
    return m.group(1).strip() if m else ""


def search_query(item: Dict[str, Any]) -> str:
    title = re.sub(r"\s*[\(\[](feat|ft|with)\.?[^\)\]]*[\)\]]", "", item.get("title", ""), flags=re.I)
    artist = (item.get("artist") or "").split(",")[0].strip()
    return f"{artist} - {title}".strip(" -")
