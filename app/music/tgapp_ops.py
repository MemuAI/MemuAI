# -*- coding: utf-8 -*-
"""Round 37: MemuMusic in the Telegram Mini App — ``music.*`` operations and the phone's audio routes.

The Mini App server (``app/tgapp_api.py``) runs inside MemuAI Proxy and is the only thing the phone reaches
through the tunnel, so everything here calls the music backend in-process (``app.music.api.call``) and never the
playground's ``/api/pg/music`` (not reachable from the phone).

* ``music.*`` operations — the same session and rate limit as the other Mini App operations. Track and playlist
  covers and stream addresses are rewritten to the Mini App's own routes with a short-lived ``music`` token.
* ``GET /tgapp/music/stream/<id>?t=`` and ``/tgapp/music/cover/<id>?t=`` — files with ``Range`` (seeking in
  ``<audio>``, partial downloads through Cloudflare quick tunnels), the token rides in the URL because ``<audio>``
  and ``<img>`` cannot send headers.
* «Пульт» (remote, like Spotify Connect) and «Продолжить на телефоне / на ПК» use the host player state of
  ``app.music.player`` through ``player.state`` / ``player.command``.
* ``music.sync`` — what the phone did while it was offline (likes, plays, playlist additions) applied in one batch.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import Request

log = logging.getLogger("memuai.music.tgapp")

TOKEN_SCOPE = "music"
TOKEN_TTL = 12 * 3600
_ID = re.compile(r"^[\w.-]{1,80}$")
_COVER_ID = re.compile(r"/cover/([\w.-]{1,80})(?:\?[^#]*?[?&]v=(\w{1,16}))?")
PC_ONLINE_SECONDS = 15

class MusicUnavailable(RuntimeError):
    """The music backend (``app.music.api``) is not installed or failed to import."""

WINDOW_OPS_ONLY = ("player.",)

class PcOffline(RuntimeError):
    """The MemuAI window (its player) is not running."""

def _api():
    try:
        from . import api
    except Exception as exc:                                  # pragma: no cover - only without the backend
        raise MusicUnavailable(str(exc)) from None
    if not hasattr(api, "call"):
        raise MusicUnavailable("app.music.api.call is missing")
    return api

def window_url() -> str:
    """The running MemuAI window (``memuai/memuai.json``); '' when it is not running."""
    import json
    import os
    if os.environ.get("MEMUAI_MUSIC_WINDOW_URL") is not None:
        return os.environ["MEMUAI_MUSIC_WINDOW_URL"].rstrip("/")
    try:
        from ..config import DATA_DIR
        data = json.loads((Path(DATA_DIR) / "memuai" / "memuai.json").read_text(encoding="utf-8"))
        url = str(data.get("url") or "").rstrip("/")
    except Exception:
        return ""
    return url if url.startswith(("http://127.0.0.1", "http://localhost")) else ""

def _window_call(url: str, op: str, args: Dict[str, Any], lang: str) -> Any:
    import httpx
    timeout = 6.0 if op.startswith("player.") else 30.0
    response = httpx.post(url + "/api/pg/music/call", json={"op": op, "args": args, "lang": lang, "sender": "phone"},
                          timeout=timeout, trust_env=False)
    data = response.json() if response.content else {}
    if response.status_code == 404:
        if "error" in data and "/api/pg/music" not in str(data.get("error")):
            raise KeyError(str(data.get("error") or "not found"))
        raise ConnectionError("no music routes in the window")
    if response.status_code >= 400:
        raise ValueError(str(data.get("error") or f"HTTP {response.status_code}"))
    return data.get("result") if isinstance(data, dict) and "result" in data else data

def call_sync(op: str, args: Optional[Dict[str, Any]] = None, lang: str = "ru") -> Any:
    args = dict(args or {})
    url = window_url()
    if url:
        try:
            return _window_call(url, op, args, lang)
        except (KeyError, ValueError):
            raise
        except Exception:
            log.debug("music: the window did not answer %s", op, exc_info=True)
    if op.startswith(WINDOW_OPS_ONLY):
        raise PcOffline(op)
    api = _api()
    try:
        return api.call(op, args, lang=lang, sender="phone")
    except TypeError:
        return api.call(op, args)

async def call(op: str, args: Optional[Dict[str, Any]] = None, lang: str = "ru") -> Any:
    """One MemuMusic operation off the event loop (the window first, then in-process)."""
    result = await asyncio.to_thread(call_sync, op, args, lang)
    if inspect.isawaitable(result):
        result = await result
    return result

def _file_of(kind: str, track_id: str) -> Optional[Path]:
    """The audio (``kind="track"``) or cover file of a track, read straight from the library on disk."""
    _api()
    from .library import get_library
    lib = get_library()
    try:
        if kind == "track":
            row = lib.row(track_id)
            path = lib.file_path(row) if row else None
        else:
            path = lib.cover_path(track_id)
    except (KeyError, LookupError, ValueError, OSError):
        return None
    if not path:
        return None
    path = Path(str(path))
    return path if path.is_file() else None

def _tracks_by_id(ids: List[str], limit: int = 120) -> List[Dict[str, Any]]:
    """Track rows for the player's queue (it keeps ids only)."""
    from .library import get_library
    lib = get_library()
    out = []
    for tid in ids[:limit]:
        try:
            out.append(lib.get(str(tid), ""))
        except Exception:
            continue
    return out

def pc_id() -> str:
    """A stable id of this computer's library (``MEMUAI_DATA_DIR/music/pc-id``): the phone keys what it saved
    for offline listening by ``pc_id + track_id``, not by the tunnel address that changes with every quick tunnel."""
    import uuid
    from ..config import DATA_DIR
    path = Path(DATA_DIR) / "music" / "pc-id"
    try:
        value = path.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[0-9a-f]{12}", value):
            return value
    except OSError:
        pass
    value = uuid.uuid4().hex[:12]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    except OSError:
        log.debug("music: cannot keep pc-id", exc_info=True)
    return value

def issue_media_token(user: str, secret: str) -> Tuple[str, int]:
    from .. import tg_webapp
    return tg_webapp.issue_token(user, secret, ttl=TOKEN_TTL, scope=TOKEN_SCOPE)

def media_token_ok(token: str) -> bool:
    from .. import tg_webapp
    from ..pgdata import load_preferences
    config = tg_webapp.load_config()
    data = tg_webapp.verify_token(token, config["secret"], scope=TOKEN_SCOPE) if token else None
    if not data or not config.get("enabled"):
        return False
    return bool(tg_webapp.is_authorized_user(str(data.get("u") or ""), load_preferences()))

class Urls:
    """Rewrites the backend's covers / streams to the Mini App's routes (relative to ``/tgapp/``)."""

    def __init__(self, token: str) -> None:
        self.token = token

    def stream(self, track_id: str) -> str:
        return f"music/stream/{track_id}?t={self.token}"

    def cover(self, track_id: str, version: str = "") -> str:
        return f"music/cover/{track_id}?t={self.token}" + (f"&v={version}" if version else "")

    def cover_from(self, value: Any, fallback_id: str = "") -> Optional[str]:
        if not value:
            return None
        text = str(value)
        match = _COVER_ID.search(text)
        if match:
            return self.cover(match.group(1), match.group(2) or "")
        if text.startswith(("https://", "data:image/")):
            return text
        return self.cover(fallback_id) if fallback_id else None

    def track(self, row: Any) -> Any:
        if not isinstance(row, dict) or not row.get("id"):
            return row
        out = dict(row)
        tid = str(out["id"])
        for private in ("path", "file", "file_path"):
            out.pop(private, None)
        out["cover"] = self.cover_from(out.get("cover"), tid)
        out["stream"] = self.stream(tid)
        return out

    def tracks(self, rows: Any) -> List[Any]:
        return [self.track(row) for row in (rows or []) if isinstance(row, dict)]

    def playlist(self, row: Any) -> Any:
        if not isinstance(row, dict):
            return row
        out = dict(row)
        out["cover"] = self.cover_from(out.get("cover"))
        if isinstance(out.get("covers"), list):
            out["covers"] = [c for c in (self.cover_from(x) for x in out["covers"][:4]) if c]
        if isinstance(out.get("tracks"), list):
            out["tracks"] = self.tracks(out["tracks"])
        return out

    def deep(self, data: Any) -> Any:
        """Tracks inside ``{tracks: [...]}``, ``recent``, ``top_tracks``, ``queue``, ``track``."""
        if isinstance(data, list):
            return [self.deep(x) for x in data]
        if not isinstance(data, dict):
            return data
        if data.get("id") and ("duration" in data or "artist" in data) and "title" in data:
            return self.track(data)
        out = dict(data)
        for key in ("tracks", "recent", "top_tracks", "queue", "liked"):
            if isinstance(out.get(key), list):
                out[key] = self.tracks(out[key])
        if isinstance(out.get("track"), dict):
            out["track"] = self.track(out["track"])
        return out

def music_look() -> Dict[str, str]:
    """Round 38: the MemuMusic window's look (Оформление: theme, own accent, accent from the cover, light/dark) for the Mini App."""
    try:
        from .. import modules_host
        ui = ((modules_host.load_persistent(modules_host.default_root(), "music").get("prefs") or {}).get("ui") or {})
    except Exception:  # noqa: BLE001 — no module storage yet: the Mini App keeps its own colours
        return {}
    if not isinstance(ui, dict):
        return {}
    clean = lambda value, allowed: value if value in allowed else ""  # noqa: E731
    accent = str(ui.get("accent") or "")
    return {"theme": re.sub(r"[^a-z]", "", str(ui.get("theme") or ""))[:16],
            "accent": accent if re.fullmatch(r"#[0-9a-fA-F]{6}", accent) else "",
            "cover": "1" if ui.get("coverTint", True) else "",
            "mode": clean(str(ui.get("mode") or ""), ("light", "dark", "system", "memu"))}

def _clean_id(value: Any) -> str:
    text = str(value or "")
    return text if _ID.match(text) else ""

def _player_view(state: Any, urls: Urls) -> Dict[str, Any]:
    """The window's player state (``player.state``) for the phone: the track, the moving position, the queue as
    tracks; ``online`` when the window has a track and reported recently (every ~15 s while it plays)."""
    if not isinstance(state, dict):
        return {"online": False, "available": True}
    if str(state.get("device") or "") == "phone":

        return {"online": False, "available": True, "device": "phone"}
    track = state.get("track") if isinstance(state.get("track"), dict) else None
    ids = [str(x) for x in (state.get("queue") or []) if x]
    index = int(state.get("index") if state.get("index") is not None else -1)
    queue: List[Dict[str, Any]] = []
    if ids:
        lo = max(0, index - 20) if index >= 0 else 0
        queue = _tracks_by_id(ids[lo:lo + 120])
        index = index - lo if index >= 0 else -1
    ts = float(state.get("updated_at") or state.get("ts") or 0)
    if ts > 1e12:
        ts /= 1000.0
    age = time.time() - ts if ts else None
    playing = bool(state.get("playing"))
    fresh = age is not None and age < (PC_ONLINE_SECONDS * 4 if playing else 6 * 3600)
    position = state.get("position_now", state.get("position", 0))
    return {"online": bool(track) and fresh, "available": True, "playing": playing and fresh,
            "track": urls.track(track) if track else None, "position": float(position or 0),
            "duration": float(state.get("duration") or (track or {}).get("duration") or 0),
            "volume": float(state.get("volume") if state.get("volume") is not None else 1),
            "shuffle": bool(state.get("shuffle")), "repeat": str(state.get("repeat") or "off"),
            "queue": urls.tracks(queue), "index": index, "device": str(state.get("device") or ""),
            "device_name": str(state.get("device_name") or ""), "age": round(age, 1) if age is not None else None}

REMOTE_COMMANDS = {"play", "pause", "toggle", "next", "prev", "seek", "volume", "play_track", "shuffle", "repeat",
                   "play_playlist"}

def register_tgapp_ops(op: Callable[[str], Callable], ApiError: Any, *, app: Any) -> None:  # noqa: N803
    """Adds ``music.*`` to the Mini App's operations and ``/tgapp/music/{stream,cover}/<id>`` to its routes."""
    from fastapi.responses import JSONResponse

    def unavailable():
        return ApiError(503, "music_unavailable", "MemuMusic недоступен на компьютере",
                        "MemuMusic is not available on the computer")

    def urls_of(ctx) -> Urls:
        token = ctx.setdefault("_music_token", issue_media_token(ctx["user"], ctx["config"]["secret"])[0])
        return Urls(token)

    async def run(ctx, name: str, args: Optional[Dict[str, Any]] = None) -> Any:
        try:
            return await call(name, args, lang="en" if ctx["prefs"].get("language") == "en" else "ru")
        except MusicUnavailable:
            raise unavailable() from None
        except PcOffline:
            raise ApiError(409, "pc_offline", "MemuAI на компьютере закрыт — откройте его, чтобы управлять плеером",
                           "MemuAI is closed on the computer — open it to control the player") from None
        except (KeyError, LookupError):
            raise ApiError(404, "not_found", "Трек не найден", "Track not found") from None
        except ValueError as exc:
            raise ApiError(400, "bad_request", str(exc)[:200] or "Некорректный запрос", str(exc)[:200] or "Bad request") from None
        except Exception as exc:
            status = int(getattr(exc, "status", 0) or 0)
            if 400 <= status < 600:
                message = str(getattr(exc, "message", "") or exc)[:300]
                raise ApiError(status, str(getattr(exc, "code", "") or "music_error"), message,
                               str(getattr(exc, "en", "") or message)) from None
            raise

    async def optional(ctx, name: str, args: Optional[Dict[str, Any]] = None, default: Any = None) -> Any:
        """An operation the backend may not have yet (or that failed): the screen still opens."""
        try:
            return await run(ctx, name, args)
        except ApiError as exc:
            if exc.code in ("music_unavailable",):
                raise
            return default
        except Exception:
            log.debug("music %s failed", name, exc_info=True)
            return default

    @app.get("/tgapp/music/stream/{track_id}", include_in_schema=False)
    async def music_stream(track_id: str, request: Request):
        return await media(track_id, request, "track")

    @app.get("/tgapp/music/cover/{track_id}", include_in_schema=False)
    async def music_cover(track_id: str, request: Request):
        return await media(track_id, request, "cover")

    async def media(track_id: str, request: Request, kind: str):
        token = request.query_params.get("t", "")
        if not _clean_id(track_id) or not await asyncio.to_thread(media_token_ok, token):
            return JSONResponse({"error": "forbidden"}, 403)
        try:
            path = await asyncio.to_thread(_file_of, kind, track_id)
        except MusicUnavailable:
            return JSONResponse({"error": "music unavailable"}, 503)
        if path is None:
            return JSONResponse({"error": "not found"}, 404)
        from .api import file_response
        from .library import MIME
        media_type = MIME.get(path.suffix.lower(), "application/octet-stream") if kind == "track" else "image/jpeg"
        cache = "private, max-age=604800" if kind == "cover" else "private, max-age=3600"
        return file_response(path, request.headers.get("range", "") if kind == "track" else "", media_type,
                             {"Cache-Control": cache})

    @op("music.home")
    async def music_home(ctx, body):
        urls = urls_of(ctx)
        stats, liked, recent_added, playlists, player = await asyncio.gather(
            optional(ctx, "library.stats", {}, {}), optional(ctx, "library.list", {"filter": "liked", "limit": 24, "sort": "liked"}, {}),
            optional(ctx, "library.list", {"sort": "added", "limit": 24}, {}), optional(ctx, "playlists.list", {}, []),
            optional(ctx, "player.state", {}, None))
        stats, liked, recent_added, playlists = stats or {}, liked or {}, recent_added or {}, playlists or []
        return {"stats": urls.deep(stats) if isinstance(stats, dict) else {},
                "liked": urls.tracks(liked.get("tracks") if isinstance(liked, dict) else []),
                "added": urls.tracks(recent_added.get("tracks") if isinstance(recent_added, dict) else []),
                "total": (recent_added or {}).get("total", 0) if isinstance(recent_added, dict) else 0,
                "playlists": [urls.playlist(p) for p in playlists if isinstance(p, dict)][:40],
                "player": _player_view(player, urls) if player is not None else {"online": False, "available": False},
                "pc_id": pc_id(), "token_ttl": TOKEN_TTL, "look": music_look()}

    @op("music.tracks")
    async def music_tracks(ctx, body):
        """Rows for track ids the phone remembers (re-saving offline tracks after the tunnel address changed)."""
        ids = [i for i in (_clean_id(x) for x in (body.get("ids") or [])[:1000]) if i]
        try:
            rows = await asyncio.to_thread(_tracks_by_id, ids, 1000)
        except MusicUnavailable:
            raise unavailable() from None
        return {"tracks": urls_of(ctx).tracks(rows), "pc_id": pc_id()}

    @op("music.list")
    async def music_list(ctx, body):
        args: Dict[str, Any] = {"limit": max(1, min(int(body.get("limit") or 100), 500)),
                                "offset": max(0, int(body.get("offset") or 0))}
        for key in ("q", "sort", "filter", "source"):
            if body.get(key):
                args[key] = str(body[key])[:200]
        data = await run(ctx, "library.list", args) or {}
        return {"tracks": urls_of(ctx).tracks(data.get("tracks")), "total": data.get("total", 0)}

    @op("music.track")
    async def music_track(ctx, body):
        tid = _clean_id(body.get("id"))
        if not tid:
            raise ApiError(400, "bad_request", "Нет id трека", "No track id")
        return {"track": urls_of(ctx).track(await run(ctx, "library.get", {"id": tid}))}

    @op("music.like")
    async def music_like(ctx, body):
        tid = _clean_id(body.get("id"))
        if not tid:
            raise ApiError(400, "bad_request", "Нет id трека", "No track id")
        await run(ctx, "library.like", {"id": tid, "liked": bool(body.get("liked"))})
        return {"ok": True, "liked": bool(body.get("liked"))}

    @op("music.lyrics")
    async def music_lyrics(ctx, body):
        tid = _clean_id(body.get("id"))
        data = await optional(ctx, "lyrics.get", {"id": tid}, None) if tid else None
        return data if isinstance(data, dict) else {"synced": None, "plain": None, "source": ""}

    @op("music.browse")
    async def music_browse(ctx, body):
        """Artists and albums (``kind``), or one of them (``name`` / ``key``)."""
        urls = urls_of(ctx)
        kind = str(body.get("kind") or "artists")
        if kind == "artist" and body.get("name"):
            data = urls.deep(await run(ctx, "artist.get", {"name": str(body["name"])[:200]}))
            if isinstance(data, dict):
                data["cover"] = urls.cover_from(data.get("cover"))
                data["albums"] = [dict(a, cover=urls.cover_from(a.get("cover"))) for a in data.get("albums") or []
                                  if isinstance(a, dict)]
            return data
        if kind == "album" and body.get("key"):
            data = await run(ctx, "album.get", {"key": str(body["key"])[:300]})
            if isinstance(data, dict):
                data = dict(data, cover=urls.cover_from(data.get("cover")), items=urls.tracks(data.get("items")))
            return data
        rows = await run(ctx, "albums.list" if kind == "albums" else "artists.list", {})
        if isinstance(rows, dict):
            rows = rows.get("items") or rows.get(kind) or []
        return {"items": [dict(r, cover=urls.cover_from(r.get("cover"))) for r in rows if isinstance(r, dict)][:300]}

    @op("music.playlists")
    async def music_playlists(ctx, body):
        rows = await run(ctx, "playlists.list", {}) or []
        urls = urls_of(ctx)
        return {"playlists": [urls.playlist(p) for p in rows if isinstance(p, dict)]}

    @op("music.playlist")
    async def music_playlist(ctx, body):
        pid = str(body.get("id") or "")[:80]
        action = str(body.get("action") or "get")
        urls = urls_of(ctx)
        if action == "create":
            name = str(body.get("name") or "").strip()[:120]
            if not name:
                raise ApiError(400, "bad_request", "Введите название", "Enter a name")
            ids = [i for i in (_clean_id(x) for x in body.get("track_ids") or []) if i]
            return {"playlist": urls.playlist(await run(ctx, "playlist.create", {"name": name, "track_ids": ids}))}
        if not pid:
            raise ApiError(400, "bad_request", "Нет id плейлиста", "No playlist id")
        if action == "delete":
            await run(ctx, "playlist.delete", {"id": pid})
            return {"ok": True}
        if action == "update":
            args: Dict[str, Any] = {"id": pid}
            if body.get("name"):
                args["name"] = str(body["name"]).strip()[:120]
            for key in ("add", "remove", "order", "track_ids"):
                if isinstance(body.get(key), list):
                    args[key] = [i for i in (_clean_id(x) for x in body[key]) if i]
            return {"playlist": urls.playlist(await run(ctx, "playlist.update", args))}
        return {"playlist": urls.playlist(await run(ctx, "playlist.get", {"id": pid}))}

    @op("music.sync")
    async def music_sync(ctx, body):
        """Likes, plays and playlist additions made on the phone (maybe offline), oldest first."""
        applied = {"likes": 0, "plays": 0, "playlists": 0}
        for row in (body.get("likes") or [])[:500]:
            tid = _clean_id((row or {}).get("id"))
            if tid and await optional(ctx, "library.like", {"id": tid, "liked": bool(row.get("liked"))}, None) is not None:
                applied["likes"] += 1
        for row in (body.get("plays") or [])[:500]:
            tid = _clean_id((row or {}).get("id"))
            if not tid:
                continue
            args = {"id": tid, "seconds": float(row.get("seconds") or 0), "device": "phone",
                    "at": float(row.get("at") or time.time())}
            if await optional(ctx, "library.played", args, None) is not None:
                applied["plays"] += 1
        for row in (body.get("playlist_adds") or [])[:200]:
            pid = str((row or {}).get("playlist") or "")[:80]
            ids = [i for i in (_clean_id(x) for x in (row or {}).get("ids") or []) if i]
            if pid and ids and await optional(ctx, "playlist.update", {"id": pid, "add": ids}, None) is not None:
                applied["playlists"] += 1
        return {"ok": True, "applied": applied}

    @op("music.detect")
    async def music_detect(ctx, body):
        url = str(body.get("url") or "").strip()[:2000]
        if not re.match(r"^https?://", url):
            raise ApiError(400, "bad_url", "Вставьте ссылку http(s)://", "Paste an http(s):// link")
        return await run(ctx, "detect", {"url": url})

    @op("music.download")
    async def music_download(ctx, body):
        url = str(body.get("url") or "").strip()[:2000]
        if not re.match(r"^https?://", url):
            raise ApiError(400, "bad_url", "Вставьте ссылку http(s)://", "Paste an http(s):// link")
        quality = str(body.get("quality") or "best")
        return await run(ctx, "download.start", {"url": url, "quality": quality if quality in ("best", "normal", "small") else "best"})

    @op("music.jobs")
    async def music_jobs(ctx, body):
        rows = await run(ctx, "download.jobs", {}) or []
        rows = rows.get("jobs", []) if isinstance(rows, dict) else rows
        urls = urls_of(ctx)
        return {"jobs": [dict(j, thumb=urls.cover_from(j.get("thumb"))) for j in rows if isinstance(j, dict)][:50]}

    @op("music.cancel")
    async def music_cancel(ctx, body):
        await run(ctx, "download.cancel", {"job_id": str(body.get("job_id") or "")[:80]})
        return {"ok": True}

    @op("music.search")
    async def music_search(ctx, body):
        q = str(body.get("q") or "").strip()[:200]
        if len(q) < 2:
            return {"results": []}
        rows = await run(ctx, "search.online", {"q": q, "limit": max(1, min(int(body.get("limit") or 12), 25))}) or []
        return {"results": rows if isinstance(rows, list) else rows.get("results", [])}

    @op("music.remote")
    async def music_remote(ctx, body):
        urls = urls_of(ctx)
        cmd = str(body.get("cmd") or "")
        if cmd:
            if cmd not in REMOTE_COMMANDS:
                raise ApiError(400, "bad_request", "Неизвестная команда", "Unknown command")
            args: Dict[str, Any] = {"cmd": cmd, "source": "phone"}
            if "value" in body:
                args["value"] = body["value"]
            if body.get("track_id"):
                args["track_id"] = _clean_id(body["track_id"])
            if body.get("position") is not None:
                args["position"] = max(0.0, float(body.get("position") or 0))
            if isinstance(body.get("queue"), list):
                args["queue"] = [i for i in (_clean_id(x) for x in body["queue"][:5000]) if i]
            if cmd == "play_playlist":
                pl = await run(ctx, "playlist.get", {"id": str(body.get("playlist_id") or "")[:80]}) or {}
                ids = [str(t.get("id")) for t in pl.get("tracks") or [] if isinstance(t, dict) and t.get("id")]
                if not ids:
                    raise ApiError(409, "empty", "Плейлист пуст", "The playlist is empty")
                args.update(cmd="play_track", track_id=ids[0], queue=ids, position=0.0,
                            context={"type": "playlist", "id": pl.get("id"), "name": pl.get("name")})
            if args["cmd"] == "play_track" and args.get("track_id") and not args.get("queue"):
                args["queue"] = [args["track_id"]]
            await run(ctx, "player.command", args)
            await asyncio.sleep(0.35)
        try:
            state = await run(ctx, "player.state", {})
        except ApiError as exc:
            if exc.code == "pc_offline" and not cmd:
                return {"online": False, "available": False}
            raise
        return _player_view(state, urls)

    @op("music.report")
    async def music_report(ctx, body):
        """The phone tells the computer what it plays (track change, play / pause, seek, every ~30 s while playing),
        so the MemuMusic window shows «Играет на телефоне» through ``player.state``. The computer's own playback
        wins: while the window plays (and reported recently) the phone's report is dropped."""
        tid = _clean_id(body.get("track_id"))
        if not tid:
            raise ApiError(400, "bad_request", "Нет трека", "No track")
        try:
            state = await run(ctx, "player.state", {})
        except ApiError as exc:
            if exc.code == "pc_offline":
                return {"ok": False, "reason": "pc_offline"}
            raise
        if isinstance(state, dict) and str(state.get("device") or "pc") != "phone" and state.get("playing"):
            ts = float(state.get("updated_at") or 0)
            if ts and time.time() - ts < PC_ONLINE_SECONDS * 3:
                return {"ok": False, "reason": "pc_playing"}
        lang_en = ctx["prefs"].get("language") == "en"
        fields: Dict[str, Any] = {"track_id": tid, "playing": bool(body.get("playing")), "device": "phone",
                                  "device_name": "Phone" if lang_en else "Телефон",
                                  "position": max(0.0, float(body.get("position") or 0)),
                                  "duration": max(0.0, float(body.get("duration") or 0))}
        if isinstance(body.get("queue"), list):
            fields["queue"] = [i for i in (_clean_id(x) for x in body["queue"][:500]) if i]
            fields["index"] = max(0, int(body.get("index") or 0))
        await run(ctx, "player.update", fields)
        return {"ok": True}

    @op("music.handoff")
    async def music_handoff(ctx, body):
        """``to: "phone"`` — take the computer's track, position and queue (the computer pauses);
        ``to: "pc"`` — the computer plays ``track_id`` from ``position`` with ``queue``."""
        urls = urls_of(ctx)
        if str(body.get("to") or "") == "pc":
            tid = _clean_id(body.get("track_id"))
            if not tid:
                raise ApiError(400, "bad_request", "Нет трека", "No track")
            queue = [i for i in (_clean_id(x) for x in (body.get("queue") or [])[:5000]) if i]
            await run(ctx, "player.command", {"cmd": "play_track", "track_id": tid, "source": "phone", "handoff": True,
                                              "position": max(0.0, float(body.get("position") or 0)),
                                              "queue": queue or [tid]})
            return {"ok": True}
        view = _player_view(await run(ctx, "player.state", {}), urls)
        if not view.get("track"):
            raise ApiError(409, "nothing_playing", "На компьютере сейчас ничего не играет", "Nothing is playing on the computer")
        if view.get("playing"):
            await optional(ctx, "player.command", {"cmd": "pause", "source": "phone", "handoff": True}, None)
        return view
