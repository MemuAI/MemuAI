# -*- coding: utf-8 -*-
"""MemuMusic operations: ``call(op, args)`` (module bridge ``Memu.music.call``, Mini App ops, main window)
and the REST routes under ``/api/pg/music/*``.

* ``POST /api/pg/music/call`` ``{op, args, module_id?}`` → JSON (a module needs the ``music`` permission).
* ``GET  /api/pg/music/stream/<id>?t=`` — audio with HTTP Range; ``GET /api/pg/music/cover/<id>?t=`` — cover.
  The token is an HMAC of the id (a secret in ``music/secret.txt``), so the URL works in ``<audio>``/``<img>``
  inside the sandboxed module iframe and through the Mini App tunnel without cookies or headers.
* ``GET  /api/pg/music/player/events`` — SSE: player state changes and remote commands.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from fastapi import Request

from . import downloader, extras, lyrics, online, sources, tools
from .library import MIME, get_library
from .player import hub

_ID = re.compile(r"^[a-f0-9]{12}$")

def _base_from(request) -> str:
    if request is None:
        return ""
    return str(request.base_url).rstrip("/")

def call(op: str, args: Optional[Dict[str, Any]] = None, *, base: str = "", lang: str = "ru", sender: str = "") -> Any:
    """Run one operation synchronously (callers put it in a thread). Errors: ValueError/KeyError/RuntimeError."""
    args = args if isinstance(args, dict) else {}
    lib = get_library()
    op = str(op or "")
    if op == "library.list":
        return lib.list(args.get("q", ""), args.get("sort", "added"), args.get("filter", ""),
                        args.get("limit", 200), args.get("offset", 0), base, str(args.get("dir") or ""))
    if op == "library.get":
        return lib.get(str(args.get("id", "")), base)
    if op == "library.update":
        return lib.update(str(args.get("id", "")), args, base)
    if op == "library.delete":
        return lib.delete(str(args.get("id", "")), bool(args.get("delete_file", True)))
    if op == "library.like":
        return lib.like(str(args.get("id", "")), bool(args.get("liked", True)))
    if op == "library.played":
        result = lib.played(str(args.get("id", "")), args.get("seconds", 0), str(args.get("device") or ""), args.get("at"))
        return result
    if op == "library.stats":
        return lib.stats(base, args.get("days", 30))
    if op == "library.history":
        return lib.history(args.get("limit", 100), base, args.get("since"), args.get("until"))
    if op == "artists.list":
        return lib.artists(base)
    if op == "albums.list":
        return lib.albums(base)
    if op == "artist.get":
        return lib.artist(str(args.get("name", "")), base)
    if op == "album.get":
        return lib.album(str(args.get("key", "")), base)
    if op == "playlists.list":
        return lib.playlists(base, lang, bool(args.get("all")))
    if op == "playlist.get":
        return lib.playlist(str(args.get("id", "")), base, lang)
    if op == "playlist.create":
        return lib.create_playlist(args.get("name", ""), args.get("track_ids") or [], args.get("description", ""),
                                   args.get("rules") or None)
    if op == "playlist.update":
        return lib.update_playlist(str(args.get("id", "")), args, base)
    if op == "playlist.delete":
        return lib.delete_playlist(str(args.get("id", "")))
    if op == "detect":
        info = sources.detect(str(args.get("url", "")))
        if args.get("resolve") and info.get("source") in sources.META:
            meta = sources.resolve_meta(info)
            info.update(kind=meta.get("kind", info["kind"]), title=meta.get("title", ""), artist=meta.get("artist", ""),
                        cover=meta.get("cover_url", ""), items_estimate=len(meta.get("items") or []))
        return info
    if op == "download.start":
        return downloader.manager().start(str(args.get("url", "")), str(args.get("quality") or ""),
                                          str(args.get("playlist_id") or ""))
    if op == "download.jobs":
        return downloader.manager().list()
    if op == "download.cancel":
        return downloader.manager().cancel(str(args.get("job_id", "")))
    if op == "download.retry":
        return downloader.manager().retry(str(args.get("job_id", "")))
    if op == "download.clear":
        return downloader.manager().clear()
    if op == "import.folder":
        return downloader.manager().start_import(str(args.get("path", "")))
    if op == "search.online":
        found = downloader.search_online(str(args.get("q", "")), args.get("limit", 12), bool(args.get("music")))
        if args.get("proxy_thumbs"):
            for item in found:
                if item.get("thumb"):
                    item["thumb"] = thumb_url(lib, item["thumb"], base)
        return found
    if op == "lyrics.get":
        return lyrics.fetch(lib, str(args.get("id", "")), force=bool(args.get("force")))
    if op == "lyrics.choose":
        return lyrics.choose(lib, str(args.get("id", "")), None if args.get("index") is None else int(args["index"]),
                             1 if args.get("next") else 0)
    if op == "lyrics.romanize":
        return lyrics.romanize(lib, str(args.get("id", "")), str(args.get("language") or "auto"))
    if op == "lyrics.offset":
        return lyrics.set_offset(lib, str(args.get("id", "")), float(args.get("offset") or 0))
    if op == "lyrics.set":
        text = args.get("text")
        if text is None and isinstance(args.get("synced"), list):
            text = "\n".join(f"[{int(float(sec) // 60):02d}:{float(sec) % 60:05.2f}]{line}"
                              for sec, line in args["synced"] if isinstance(line, str))
        return lyrics.set_manual(lib, str(args.get("id", "")), str(text if text is not None else args.get("plain") or ""))
    if op == "settings.get":
        return {**lib.settings(), "library_dir": str(lib.root), "default_download_dir": str(lib.tracks_dir)}
    if op == "settings.set":
        before = lib.settings().get("normalize_target")
        result = lib.set_settings(args)
        if result.get("normalize_target") != before:
            target = float(result["normalize_target"])
            lib._x("UPDATE tracks SET gain=MAX(-18, MIN(12, ROUND(? - lufs, 2))) WHERE lufs IS NOT NULL", (target,))
        return result
    if op == "tools.status":
        from . import tagger
        status = tools.status()
        try:
            import mutagen
            status["mutagen"] = {"ok": True, "version": getattr(mutagen, "version_string", "")}
        except Exception:
            status["mutagen"] = {"ok": False, "version": ""}
        status["tagging"] = tagger.available()
        status["lyrics_prefetch"] = lyrics.prefetcher().status()
        status["loudness"] = extras.analyzer().status()
        return status
    if op == "tools.update":
        return tools.update_ytdlp()
    extra = _extra_op(lib, op, args, base)
    if extra is not _NO_OP:
        return extra
    if op == "stream_url":
        tid = str(args.get("id", ""))
        lib.get(tid)
        return lib.stream_url(tid, base)
    if op == "cover_url":
        tid = str(args.get("id", ""))
        lib.get(tid)
        return lib.cover_url(tid, base)
    player = hub(lib.root)
    if op == "player.state":
        state = player.snapshot()
        if state.get("track_id"):
            try:
                state["track"] = lib.get(state["track_id"], base)
            except KeyError:
                state["track"] = None
        return state
    if op == "player.update":
        return player.update(args)
    if op == "player.command":
        return player.command(str(args.get("cmd", "")), args, sender)
    if op == "player.wait":
        return player.wait(int(args.get("since", 0) or 0), int(args.get("since_cmd", -1) if args.get("since_cmd") is not None else -1),
                           float(args.get("timeout", 25) or 25))
    raise ValueError(f"Неизвестная операция MemuMusic: {op} / Unknown MemuMusic operation: {op}")

_NO_OP = object()
_THUMBS: "Dict[str, tuple]" = {}

def thumb_url(lib, url: str, base: str = "") -> str:
    """A web image (cover search / online search preview) through our server: the module window may only load
    images from /api/pg/music/ (its CSP). The HMAC of the URL keeps the route from being an open proxy."""
    import urllib.parse
    return f"{base}/api/pg/music/thumb?u={urllib.parse.quote(str(url), safe='')}&t={lib.token('thumb', str(url))}"

def thumb_image(url: str, token: str):
    from fastapi.responses import JSONResponse, Response
    lib = get_library()
    if not url.startswith(("https://", "http://")) or not lib.check_token("thumb", url, token):
        return JSONResponse({"error": "forbidden"}, 403, headers=cors_headers())
    hit = _THUMBS.get(url)
    if hit is None:
        client = sources._client()
        try:
            resp = client.get(url)
            kind = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            data = resp.content
        except Exception:
            return JSONResponse({"error": "unavailable"}, 502, headers=cors_headers())
        finally:
            try:
                client.close()
            except Exception:
                pass
        if resp.status_code >= 400 or not kind.startswith("image/") or len(data) > 5 * 1024 * 1024:
            return JSONResponse({"error": "not an image"}, 404, headers=cors_headers())
        if len(_THUMBS) > 300:
            _THUMBS.clear()
        hit = _THUMBS[url] = (data, kind)
    return Response(hit[0], media_type=hit[1], headers={"Cache-Control": "private, max-age=86400", **cors_headers()})

def _extra_op(lib, op: str, args: Dict[str, Any], base: str) -> Any:
    """Round 38 operations (app/music/extras.py)."""
    tid = str(args.get("id", ""))
    if op == "library.update_many":
        return extras.update_many(lib, args.get("ids") or [], args.get("changes") or {}, base)
    if op == "library.genres":
        return extras.genres(lib, base)
    if op == "library.folders":
        return extras.folders(lib)
    if op == "library.duplicates":
        return extras.duplicates(lib, base)
    if op == "library.radio":
        return extras.radio(lib, tid, args.get("limit", 30), base)
    if op == "library.reveal":
        return extras.reveal(lib, tid, via=str(args.get("via") or "auto"))
    if op == "library.resume":
        return extras.set_resume(lib, tid, args.get("position", 0))
    if op == "library.upload":
        return extras.upload(lib, str(args.get("name", "")), str(args.get("data", "")), base)
    if op == "library.analyze":
        return extras.analyze(lib, args.get("ids") or None, not args.get("all"))
    if op == "library.redownload":
        row = lib.row(tid)
        if not row:
            raise KeyError("Трек не найден / Track not found")
        if not row.get("source_url"):
            raise ValueError("У трека нет ссылки-источника / The track has no source link")
        return downloader.manager().start(row["source_url"], str(args.get("quality") or "best"), replace_id=tid)
    if op == "track.cover":
        return extras.set_cover(lib, tid, args, base=base)
    if op == "cover.search":
        found = extras.cover_search(str(args.get("q", "")), args.get("limit", 12))
        for item in found:
            item["thumb"] = thumb_url(lib, item["thumb"], base)
        return found
    if op == "playlist.cover":
        return extras.set_playlist_cover(lib, tid, args, base=base)
    if op == "playlist.preview":
        return extras.rules_preview(lib, args.get("rules") or {}, base)
    if op == "playlist.export":
        return extras.export_m3u(lib, tid, str(args.get("path") or ""), str(args.get("format") or "m3u8"))
    if op == "playlist.import":
        return extras.import_m3u(lib, str(args.get("text") or ""), str(args.get("path") or ""), str(args.get("name") or ""))
    if op in R39_OPS:
        from . import artists
        name = str(args.get("name", ""))
        if op == "artist.rename":
            return artists.rename(lib, name, str(args.get("new_name", "")), base)
        if op == "artist.image":
            return artists.set_image(lib, name, args)
        if op == "artist.image_search":
            found = artists.image_search(str(args.get("q") or name), args.get("limit", 12))
            for item in found:
                item["thumb"] = thumb_url(lib, item["thumb"], base)
            return found
        if op == "artist.image_auto":
            return artists.image_auto(lib, name)
        if op == "artist.bio":
            return artists.set_bio(lib, name, str(args.get("bio") or ""))
        if op == "album.update":
            return artists.album_update(lib, args.get("ids") or [], args, base=base)
        if op == "playlist.reorder":
            return artists.reorder_playlist(lib, tid, args, base)
        if op == "playlists.reorder":
            return artists.reorder_playlists(lib, args.get("ids") or [])
    if op == "playlist.save_online":
        return downloader.manager().start(str(args.get("url", "")), str(args.get("quality") or ""), str(args.get("playlist_id") or ""))
    if op.startswith("online."):
        return _online_op(lib, op, args, base)
    return _NO_OP

def _online_op(lib, op: str, args: Dict[str, Any], base: str) -> Any:
    if op == "online.stream":
        from . import online_audio
        return online_audio.resolve(str(args.get("url") or ""), base, "/api/pg/music/online-audio")
    def thumbs(item: Dict[str, Any]) -> Dict[str, Any]:
        if args.get("proxy_thumbs", True):
            for key in ("thumb", "cover"):
                if item.get(key):
                    item[key] = thumb_url(lib, item[key], base)
        return item
    if op == "online.search":
        found = online.search(lib, str(args.get("q", "")), args.get("limit", 12), str(args.get("source") or "ytmusic"))
        found["results"] = [thumbs(x) for x in found["results"]]
        return found
    if op == "online.details":
        hint = {k: v for k, v in (args.get("hint") or {}).items() if k != "thumb"}
        return thumbs(online.details(lib, str(args.get("url", "")), hint))
    if op == "online.related":
        return [thumbs(x) for x in online.related(lib, str(args.get("url", "")), args.get("limit", 20))]
    if op == "online.lyrics":
        return online.lyrics_lookup(lib, str(args.get("title", "")), str(args.get("artist", "")), float(args.get("duration") or 0))
    return _NO_OP

OPS = ("library.list", "library.get", "library.update", "library.delete", "library.like", "library.played",
       "library.stats", "library.history", "artists.list", "albums.list", "artist.get", "album.get", "playlists.list",
       "playlist.get", "playlist.create", "playlist.update", "playlist.delete", "detect", "download.start",
       "download.jobs", "download.cancel", "download.retry", "download.clear", "import.folder", "search.online",
       "lyrics.get", "lyrics.set", "lyrics.choose", "lyrics.romanize", "lyrics.offset", "settings.get", "settings.set", "tools.status", "tools.update", "stream_url", "cover_url",
       "player.state", "player.update", "player.command", "player.wait",

       "library.update_many", "library.genres", "library.folders", "library.duplicates", "library.radio", "library.reveal",
       "library.resume", "library.upload", "library.analyze", "library.redownload", "track.cover", "cover.search",
       "playlist.cover", "playlist.preview", "playlist.export", "playlist.import", "playlist.save_online")
R39_OPS = ("artist.rename", "artist.image", "artist.image_search", "artist.image_auto", "artist.bio", "album.update",
           "playlist.reorder", "playlists.reorder")
R41_OPS = ("online.search", "online.details", "online.related", "online.lyrics", "online.stream")
OPS = OPS + R39_OPS + R41_OPS

async def call_async(op: str, args: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Any:
    if op == "player.wait":
        a = args if isinstance(args, dict) else {}
        root = (await asyncio.to_thread(get_library)).root
        return await hub(root).wait_async(int(a.get("since", 0) or 0), int(a.get("since_cmd", -1) if a.get("since_cmd") is not None else -1),
                                          float(a.get("timeout", 25) or 25))
    return await asyncio.to_thread(call, op, args, **kwargs)

def error_payload(exc: Exception) -> tuple:
    if isinstance(exc, KeyError):
        return {"error": str(exc.args[0] if exc.args else exc)}, 404
    if isinstance(exc, (ValueError, TypeError)):
        return {"error": str(exc)}, 400
    return {"error": f"{exc}"[:600] or type(exc).__name__}, 502

def file_response(path: Path, range_header: str, media_type: str, extra_headers: Optional[Dict[str, str]] = None):
    """A StreamingResponse honouring ``Range: bytes=a-b`` (206), with Accept-Ranges and Content-Length."""
    from fastapi.responses import Response, StreamingResponse
    size = path.stat().st_size
    start, end = 0, size - 1
    status = 200
    m = re.match(r"^bytes=(\d*)-(\d*)$", (range_header or "").strip())
    if m and (m.group(1) or m.group(2)):
        if m.group(1):
            start = int(m.group(1))
            end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
        else:
            start = max(0, size - int(m.group(2)))
        if start > end or start >= size:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        status = 206
    length = end - start + 1

    def chunks():
        with open(path, "rb") as handle:
            handle.seek(start)
            left = length
            while left > 0:
                data = handle.read(min(256 * 1024, left))
                if not data:
                    break
                left -= len(data)
                yield data

    headers = {"Accept-Ranges": "bytes", "Content-Length": str(length), "Cache-Control": "private, max-age=3600",
               "Cross-Origin-Resource-Policy": "cross-origin", **(extra_headers or {})}
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(chunks(), status_code=status, media_type=media_type, headers=headers)

def stream_track(track_id: str, token: str, range_header: str = ""):
    """Shared by the playground route and the Mini App server (app/music/tgapp_ops.py)."""
    from fastapi.responses import JSONResponse
    lib = get_library()
    if not _ID.match(track_id or "") or not lib.check_token("stream", track_id, token):
        return JSONResponse({"error": "forbidden"}, 403, headers=cors_headers())
    row = lib.row(track_id)
    path = lib.file_path(row) if row else None
    if not path or not path.is_file():
        return JSONResponse({"error": "not found"}, 404, headers=cors_headers())
    return file_response(path, range_header, MIME.get(path.suffix.lower(), "application/octet-stream"), cors_headers())

def cover_image(track_id: str, token: str):
    from fastapi.responses import JSONResponse
    lib = get_library()
    if not _ID.match(track_id or "") or not lib.check_token("cover", track_id, token):
        return JSONResponse({"error": "forbidden"}, 403, headers=cors_headers())
    path = lib.cover_path(track_id)
    if not path:
        return JSONResponse({"error": "not found"}, 404, headers=cors_headers())
    return file_response(path, "", "image/jpeg", {"Cache-Control": "private, max-age=604800", **cors_headers()})

CORS_EXPOSE = "Content-Range, Accept-Ranges, Content-Length"

def cors_headers(preflight: bool = False) -> Dict[str, str]:
    """CORS for stream/cover: the module window is a sandboxed iframe (Origin: null) and reads the media with
    crossorigin="anonymous" (WebAudio EQ, visualiser, crossfade, cover colours). The ?t= HMAC token protects them."""
    headers = {"Access-Control-Allow-Origin": "*", "Access-Control-Expose-Headers": CORS_EXPOSE,
               "Cross-Origin-Resource-Policy": "cross-origin", "Timing-Allow-Origin": "*"}
    if preflight:
        headers.update({"Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
                        "Access-Control-Allow-Headers": "Range, If-Range, If-None-Match, If-Modified-Since",
                        "Access-Control-Max-Age": "86400", "Content-Length": "0"})
    return headers

def is_public_media_path(path: str) -> bool:
    """Paths the playground boundary lets through cross-site: they check their own HMAC token."""
    return bool(re.match(r"^/api/pg/music/(stream|cover)/[a-f0-9]{12}$", path or "")) or bool(re.match(r"^/api/pg/music/online-audio/[A-Za-z0-9_-]+$", path or "")) or path == "/api/pg/music/thumb"

def csp_sources() -> str:
    """Extra CSP sources for a module with the ``music`` permission (stream + cover URLs)."""
    return "http://127.0.0.1:*/api/pg/music/ http://localhost:*/api/pg/music/"

_ALLOWED: Dict[tuple, tuple] = {}
_ALLOWED_TTL = 5.0

def module_allowed(module_id: str, action: str = "music_call") -> bool:

    import time
    from ..modules_host import ACTION_PERMISSION, default_root, load_package
    root = default_root()
    key = (str(root), module_id, action)
    hit = _ALLOWED.get(key)
    if hit and time.monotonic() - hit[1] < _ALLOWED_TTL:
        return hit[0]
    try:
        package = load_package(root, module_id)
        ok = ACTION_PERMISSION.get(action, "music") in (package.get("permissions") or [])
    except (KeyError, ValueError):
        ok = False
    _ALLOWED[key] = (ok, time.monotonic())
    return ok

def register(app, *, allowed: Optional[Callable[[str, str], bool]] = module_allowed) -> None:
    from fastapi.responses import JSONResponse, StreamingResponse

    @app.post("/api/pg/music/call")
    async def music_call(request: Request):
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "Некорректный JSON / Invalid JSON"}, 400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Ожидается JSON-объект / A JSON object is expected"}, 400)
        module_id = str(body.get("module_id") or "")
        if module_id and allowed is not None and not allowed(module_id, "music_call"):
            return JSONResponse({"error": "Модулю не выдано право music / The module lacks the music permission"}, 403)
        lang = "en" if str(body.get("lang") or "") == "en" else "ru"
        try:
            result = await call_async(str(body.get("op") or ""), body.get("args") or {}, base=_base_from(request),
                                      lang=lang, sender=module_id or "app")
        except Exception as exc:
            payload, code = error_payload(exc)
            return JSONResponse(payload, code)
        return JSONResponse({"result": result})

    @app.api_route("/api/pg/music/stream/{track_id}", methods=["OPTIONS"])
    @app.api_route("/api/pg/music/cover/{track_id}", methods=["OPTIONS"])
    async def music_media_preflight(track_id: str):
        from fastapi.responses import Response
        return Response(status_code=204, headers=cors_headers(preflight=True))

    @app.options("/api/pg/music/online-audio/{token}")
    async def music_online_audio_preflight(token: str):
        from fastapi.responses import Response
        return Response(status_code=204, headers=cors_headers(preflight=True))

    @app.api_route("/api/pg/music/stream/{track_id}", methods=["GET", "HEAD"])
    async def music_stream(track_id: str, request: Request, t: str = ""):
        return await asyncio.to_thread(stream_track, track_id, t, request.headers.get("range", ""))

    @app.api_route("/api/pg/music/online-audio/{token}", methods=["GET", "HEAD"])
    async def music_online_audio(token: str, request: Request):
        from . import online_audio
        return await online_audio.stream(token, request, cors=True)

    @app.api_route("/api/pg/music/cover/{track_id}", methods=["GET", "HEAD"])
    async def music_cover(track_id: str, t: str = ""):
        return await asyncio.to_thread(cover_image, track_id, t)

    @app.get("/api/pg/music/thumb")
    async def music_thumb(u: str = "", t: str = ""):
        return await asyncio.to_thread(thumb_image, u, t)

    @app.get("/api/pg/music/player/events")
    async def music_player_events(request: Request):
        player = hub(get_library().root)

        async def events():
            since = player.version
            since_cmd = player.cmd_seq

            yield "retry: 1000\ndata: " + json.dumps({"event": "state", "state": player.snapshot()}, ensure_ascii=False) + "\n\n"
            while not await request.is_disconnected():
                result = await player.wait_async(since, since_cmd, 15)
                if not result["changed"]:
                    yield ": ping\n\n"
                    continue
                since = result["state"]["version"]
                for cmd in result["commands"]:
                    since_cmd = max(since_cmd, cmd["seq"])
                    yield "data: " + json.dumps({"event": "command", "command": cmd}, ensure_ascii=False) + "\n\n"
                yield "data: " + json.dumps({"event": "state", "state": result["state"]}, ensure_ascii=False) + "\n\n"

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})
