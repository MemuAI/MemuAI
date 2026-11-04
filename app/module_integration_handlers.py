"""Round 38: server handlers of built-in module commands (``run.handler`` in ``contributes.commands``).

Each handler gets the checked arguments and ``ctx`` ({lang, en, tr, module, root, surface, base}) and returns
``{text, card?, client?}``: ``text`` is what the AI reads (and the plain answer), ``card`` is drawn by the chat /
Quick Access (``{kind: "track"|"list"|"info", title, subtitle, cover, accent, items, actions:[{label, command, args}]}``),
``client`` asks the window for something only it can do (start the host player, open a module).
"""
from __future__ import annotations

import asyncio
import difflib
import re
from typing import Any, Dict, List

from .module_integration import CommandError, handler

def _music(op: str, args: Dict[str, Any], ctx: Dict[str, Any]) -> Any:
    from .music import api as music_api
    return music_api.call(op, args, base=ctx.get("base") or "", lang=ctx["lang"], sender="memuai")

async def _m(op: str, args: Dict[str, Any], ctx: Dict[str, Any]) -> Any:
    return await asyncio.to_thread(_music, op, args, ctx)

def _track_card(track: Dict[str, Any], ctx: Dict[str, Any], *, playing: bool = True, note: str = "",
                state: Dict[str, Any] = None) -> Dict[str, Any]:
    tr = ctx["tr"]
    state = state or {}
    card = {"kind": "track", "module": "music", "id": track.get("id"), "title": track.get("title") or "—",
            "artist": track.get("artist") or "",
            "subtitle": " · ".join(x for x in (track.get("artist"), track.get("album")) if x),
            "cover": track.get("cover") or "", "accent": track.get("accent") or "", "duration": track.get("duration") or 0,
            "liked": bool(track.get("liked")), "note": note, "playing": playing,
            "actions": [{"icon": "prev", "label": tr("Назад", "Previous"), "command": "prev"},
                        {"icon": "pause" if playing else "play", "label": tr("Пауза", "Pause") if playing else tr("Играть", "Play"),
                         "command": "pause" if playing else "resume", "toggle": True},
                        {"icon": "next", "label": tr("Дальше", "Next"), "command": "next"},
                        {"icon": "open", "label": tr("Открыть MemuMusic", "Open MemuMusic"), "open": True}]}

    if state.get("position_now") is not None:
        card["position"] = float(state.get("position_now") or 0)
    if isinstance(state.get("volume"), (int, float)):
        card["volume"] = float(state["volume"])
    return card

def _score(track: Dict[str, Any], query: str) -> float:
    q = query.lower()
    hay = f"{track.get('artist') or ''} {track.get('title') or ''}".lower()
    title = str(track.get("title") or "").lower()
    ratio = max(difflib.SequenceMatcher(None, q, hay).ratio(), difflib.SequenceMatcher(None, q, title).ratio())
    words = [w for w in re.split(r"\W+", q) if w]
    hits = sum(1 for w in words if w in hay) / max(1, len(words))
    return ratio * 0.5 + hits * 0.5 + (0.2 if title == q else 0)

async def _find_tracks(query: str, ctx: Dict[str, Any], limit: int = 50) -> List[Dict[str, Any]]:
    query = re.sub(r"\s+", " ", query).strip(" «»\"'")
    found = (await _m("library.list", {"q": query, "limit": limit, "sort": "plays"}, ctx)).get("tracks") or []
    if not found:

        words = [w for w in re.split(r"[\s\-—–,]+|\bby\b|\bот\b", query) if len(w) > 2][:4]
        seen = {}
        for word in words:
            for track in (await _m("library.list", {"q": word, "limit": 100}, ctx)).get("tracks") or []:
                seen[track["id"]] = track
        found = [t for t in seen.values() if _score(t, query) >= 0.45]
    if not found:

        rows = (await _m("library.list", {"limit": 3000, "sort": "plays"}, ctx)).get("tracks") or []
        found = [t for t in rows if _score(t, query) >= 0.42]
    return sorted(found, key=lambda t: -_score(t, query))

async def _play_ids(ids: List[str], start: str, context: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
    command = {"cmd": "play_track", "args": {"track_id": start, "queue": ids[:1000], "context": {"name": context}}, "target": "pc"}
    await _m("player.command", {"cmd": "play_track", **command["args"], "target": "pc"}, ctx)
    return command

def _boot(command: Dict[str, Any]) -> Dict[str, Any]:
    """The main window starts the host player (pgstatic/music-dock.js) when nothing listens yet and applies ``command``
    (the dock ignores commands sent before it started listening)."""
    return {"music_boot": True, "command": command}

@handler("music.play")
async def music_play(args: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    tr = ctx["tr"]
    query = str(args.get("query") or "").strip()
    if not query:
        await _m("player.command", {"cmd": "play", "target": "pc"}, ctx)
        command = {"cmd": "play_track", "args": {"track_id": "", "position": 0}, "target": "pc"}
        state = await _m("player.state", {}, ctx)
        track = state.get("track") or {}
        if not track:
            return {"ok": False, "error": tr("Нечего продолжать: назовите трек", "Nothing to resume: name a track")}
        return {"text": tr(f"Продолжаю: {track.get('title')}", f"Resuming: {track.get('title')}"),
                "card": _track_card(track, ctx), "client": _boot({**command, "args": {
                    "track_id": track.get("id"), "queue": state.get("queue") or [track.get("id")],
                    "position": state.get("position_now") or 0}})}
    tracks = await _find_tracks(query, ctx)
    if not tracks:
        return {"ok": False, "not_found": True,
                "error": tr(f"В библиотеке MemuMusic нет «{query}». Можно скачать по ссылке: «скачай <ссылка>».",
                            f"“{query}” is not in the MemuMusic library. You can download it by link: “download <link>”."),
                "card": {"kind": "info", "module": "music", "title": tr("Не нашёл в библиотеке", "Not in the library"),
                         "subtitle": query, "actions": [{"icon": "open", "label": tr("Открыть MemuMusic", "Open MemuMusic"), "open": True}]}}
    best = tracks[0]
    artist = str(best.get("artist") or "")
    queue = [t["id"] for t in tracks[:50]]
    if len(queue) < 5 and artist:
        more = (await _m("library.list", {"q": artist, "limit": 40, "sort": "plays"}, ctx)).get("tracks") or []
        queue += [t["id"] for t in more if t["id"] not in queue]
    command = await _play_ids(queue, best["id"], tr(f"По запросу «{query}»", f"Request “{query}”"), ctx)
    return {"text": tr(f"Играет: {best.get('title')} — {artist}", f"Playing: {best.get('title')} — {artist}"),
            "card": _track_card(best, ctx), "client": _boot(command)}

async def _simple(cmd: str, ctx: Dict[str, Any], text_ru: str, text_en: str, **extra: Any) -> Dict[str, Any]:

    await _m("player.command", {"cmd": cmd, "target": "pc", **extra}, ctx)
    state = await _m("player.state", {}, ctx)
    track = state.get("track") or {}
    out = {"text": ctx["tr"](text_ru, text_en)}
    if track:
        playing = False if cmd == "pause" else True if cmd in ("play", "resume") else bool(state.get("playing")) and not state.get("stale")
        out["card"] = _track_card(track, ctx, playing=playing, state=state)
    out["client"] = _boot({"cmd": cmd, "args": extra, "target": "pc"}) if cmd != "pause" else {}
    return out

async def _library_order(ctx: Dict[str, Any]) -> List[str]:
    """All tracks in the library order (the MemuMusic «Library» view): what «next» plays once the queue
    ran out — a one-track queue used to restart the same song."""
    rows = (await _m("library.list", {"limit": 2000, "sort": "added"}, ctx)).get("tracks") or []
    return [str(t["id"]) for t in rows if t.get("id")]

async def _skip(direction: int, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Round 39: «next» / «previous» resolved here, against the queue the player reported.
    prev: past 3 s — back to the start of the track, otherwise the previous track; next: the next in the queue, after its end —
    the following track of the library (or the first one again). The module window keeps its own queue (shuffle order,
    radio), so it gets the plain command; the host player (dock) and a not-yet-started one get an explicit ``play_track``."""
    tr = ctx["tr"]
    state = await _m("player.state", {}, ctx)
    current = str(state.get("track_id") or "")
    queue = [str(x) for x in state.get("queue") or [] if x] or ([current] if current else [])
    if not current:
        cmd = "next" if direction > 0 else "prev"
        await _m("player.command", {"cmd": cmd, "target": "pc"}, ctx)
        return {"text": tr("Следующий трек", "Next track") if direction > 0 else tr("Предыдущий трек", "Previous track"),
                "client": _boot({"cmd": cmd, "args": {}, "target": "pc"})}
    index = queue.index(current) if current in queue else max(0, min(len(queue) - 1, int(state.get("index") or 0)))
    position = float(state.get("position_now") or 0)
    playing = bool(state.get("playing")) and not state.get("stale")
    module_plays = state.get("device_name") == "MemuMusic" and bool(state.get("online"))
    if direction < 0 and position > 3:
        await _m("player.command", {"cmd": "seek", "position": 0, "target": "pc"}, ctx)
        track = state.get("track") or {}
        return {"text": tr("С начала", "From the start") + (f": {track.get('title')}" if track else ""),
                "card": _track_card(track, ctx, playing=playing, state={**state, "position_now": 0}) if track else None,
                "client": {}}
    target_index = index + direction
    if 0 <= target_index < len(queue):
        target = queue[target_index]
    else:
        library = await _library_order(ctx)
        at = library.index(current) if current in library else -1
        if direction > 0:
            tail = library[at + 1:] if at >= 0 else library
            if not tail:
                tail = [x for x in library if x != current][:1] or [current]
            queue = queue + [x for x in tail[:200] if x not in queue]
            target_index = index + 1 if index + 1 < len(queue) else index
        else:
            before = library[at - 1] if at > 0 else ""
            if not before:
                await _m("player.command", {"cmd": "seek", "position": 0, "target": "pc"}, ctx)
                track = state.get("track") or {}
                return {"text": tr("Это первый трек", "This is the first track"),
                        "card": _track_card(track, ctx, playing=playing, state={**state, "position_now": 0}) if track else None, "client": {}}
            queue = [before] + queue
            target_index = 0
        target = queue[target_index]
    try:
        track = await _m("library.get", {"id": target}, ctx)
    except Exception:  # noqa: BLE001 - the track left the library
        track = {}
    if module_plays:
        await _m("player.command", {"cmd": "next" if direction > 0 else "prev", "target": "pc"}, ctx)
        command = {"cmd": "next" if direction > 0 else "prev", "args": {}, "target": "pc"}
    else:
        context = (state.get("context") or {}).get("name") or ""
        command = {"cmd": "play_track", "args": {"track_id": target, "queue": queue[:1000], "context": {"name": context}}, "target": "pc"}
        await _m("player.command", {"cmd": "play_track", **command["args"], "target": "pc"}, ctx)
    label = tr("Следующий трек", "Next track") if direction > 0 else tr("Предыдущий трек", "Previous track")
    return {"text": label + (f": {track.get('title')}" if track else ""),
            "card": _track_card(track, ctx, playing=True, state={"position_now": 0, "volume": state.get("volume")}) if track else None,
            "client": _boot(command)}

@handler("music.pause")
async def music_pause(args, ctx):
    return await _simple("pause", ctx, "Пауза", "Paused")

@handler("music.resume")
async def music_resume(args, ctx):
    return await music_play({}, ctx)

@handler("music.next")
async def music_next(args, ctx):
    return await _skip(1, ctx)

@handler("music.prev")
async def music_prev(args, ctx):
    return await _skip(-1, ctx)

@handler("music.volume")
async def music_volume(args, ctx):
    level = max(0, min(100, int(float(args.get("level", 50)))))
    return await _simple("volume", ctx, f"Громкость {level}%", f"Volume {level}%", value=level / 100)

@handler("music.now")
async def music_now(args, ctx):
    tr = ctx["tr"]
    state = await _m("player.state", {}, ctx)
    track = state.get("track") or {}
    if not track:
        return {"text": tr("Сейчас ничего не играет", "Nothing is playing")}
    playing = bool(state.get("playing")) and not state.get("stale")
    where = state.get("device_name") or ""
    text = tr(f"{'Играет' if playing else 'На паузе'}: {track.get('title')} — {track.get('artist') or '—'}"
              + (f" ({where})" if where else ""),
              f"{'Playing' if playing else 'Paused'}: {track.get('title')} — {track.get('artist') or '—'}" + (f" ({where})" if where else ""))
    return {"text": text, "card": _track_card(track, ctx, playing=playing)}

@handler("music.playlist")
async def music_playlist(args, ctx):
    tr = ctx["tr"]
    name = str(args.get("name") or "").strip().lower()
    rows = await _m("playlists.list", {}, ctx)
    if not name:
        items = [{"title": p["name"], "subtitle": tr(f"{p['count']} треков", f"{p['count']} tracks"), "cover": p.get("cover") or "",
                  "command": "playlist", "args": {"name": p["name"]}} for p in rows[:12]]
        return {"text": tr("Плейлисты: ", "Playlists: ") + ", ".join(p["name"] for p in rows[:12]),
                "card": {"kind": "list", "module": "music", "title": tr("Плейлисты", "Playlists"), "items": items}}
    best = max(rows, key=lambda p: difflib.SequenceMatcher(None, name, str(p["name"]).lower()).ratio()
               + (0.5 if name in str(p["name"]).lower() else 0), default=None)
    if not best or difflib.SequenceMatcher(None, name, str(best["name"]).lower()).ratio() < 0.45 and name not in str(best["name"]).lower():
        return {"ok": False, "error": tr(f"Плейлист «{name}» не найден", f"Playlist “{name}” was not found")}
    full = await _m("playlist.get", {"id": best["id"]}, ctx)
    ids = [t["id"] for t in full.get("tracks") or []]
    if not ids:
        return {"ok": False, "error": tr(f"Плейлист «{best['name']}» пуст", f"The playlist “{best['name']}” is empty")}
    if args.get("shuffle"):
        import random
        random.shuffle(ids)
    command = await _play_ids(ids, ids[0], best["name"], ctx)
    first = full["tracks"][[t["id"] for t in full["tracks"]].index(ids[0])]
    card = _track_card(first, ctx, note=tr(f"Плейлист «{best['name']}» · {len(ids)}", f"Playlist “{best['name']}” · {len(ids)}"))
    return {"text": tr(f"Включаю плейлист «{best['name']}» ({len(ids)} треков)", f"Playing the playlist “{best['name']}” ({len(ids)} tracks)"),
            "card": card, "client": _boot(command)}

@handler("music.download")
async def music_download(args, ctx):
    tr = ctx["tr"]
    url = str(args.get("url") or "").strip()
    if not re.match(r"^https?://", url):
        raise CommandError(tr("Нужна ссылка http(s)", "An http(s) link is required"))
    info = await _m("detect", {"url": url}, ctx)
    if info.get("source") in (None, "", "search"):
        return {"ok": False, "error": tr("Эту ссылку MemuMusic не скачивает", "MemuMusic cannot download this link")}
    job = await _m("download.start", {"url": url}, ctx)
    return {"text": tr("Скачиваю в MemuMusic — трек появится в библиотеке", "Downloading to MemuMusic — it will appear in the library"),
            "card": {"kind": "info", "module": "music", "title": tr("Загрузка началась", "Download started"),
                     "subtitle": str(info.get("title") or info.get("source") or url)[:120], "job": job.get("id") if isinstance(job, dict) else "",
                     "actions": [{"icon": "open", "label": tr("Открыть загрузки", "Open downloads"), "open": True,
                                  "detail": {"route": "download"}}]}}

# The settings an AI call may read or change: playback, lyrics and display only. Paths,
# cookies, hotkeys and the download folder stay out on purpose — they live in the
# MemuMusic window. The library still validates ranges and choices; this list is
# only the gate before it.
_SAFE_SETTINGS = frozenset({"volume", "normalize", "normalize_target", "crossfade", "gapless", "auto_radio",
                            "auto_lyrics", "lyrics_sync", "auto_analyze", "resume_long", "seek_step", "play_click",
                            "show_genres", "show_folders", "show_history", "artist_photos"})

def _list_items(tracks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{"title": t.get("title") or "—", "subtitle": " · ".join(x for x in (t.get("artist"), t.get("album")) if x),
             "cover": t.get("cover") or ""} for t in tracks]

def _candidates(tracks: List[Dict[str, Any]], ctx: Dict[str, Any], title_ru: str, title_en: str) -> Dict[str, Any]:
    """Several tracks match a name: show them (with ids in the text the AI reads) and do nothing."""
    tr = ctx["tr"]
    lines = "\n".join(f"{i + 1}. {t.get('title')} — {t.get('artist') or '—'} (id: {t.get('id')})"
                      for i, t in enumerate(tracks))
    return {"ok": False, "error": tr("Неоднозначно: уточните трек", "Ambiguous: pick a track"),
            "text": tr(title_ru, title_en) + "\n" + lines,
            "card": {"kind": "list", "module": "music", "title": tr("Уточните трек", "Pick a track"), "items": _list_items(tracks)}}

async def _resolve_track(args: Dict[str, Any], ctx: Dict[str, Any]):
    """An exact id, or one track for a name — several matches come back as a candidates card."""
    track_id = str(args.get("track_id") or "").strip()
    if track_id:
        return await _m("library.get", {"id": track_id}, ctx), None
    query = str(args.get("query") or "").strip()
    if not query:
        raise CommandError(ctx["tr"]("Нужен id трека или его название", "A track id or a track name is required"))
    tracks = await _find_tracks(query, ctx, limit=8)
    if not tracks:
        raise CommandError(ctx["tr"](f"Трек «{query}» не найден в библиотеке", f"“{query}” is not in the library"))
    if len(tracks) > 1:
        return None, _candidates(tracks, ctx, "Нашлось несколько треков:", "Several tracks match:")
    return tracks[0], None

async def _resolve_playlist(args: Dict[str, Any], ctx: Dict[str, Any]):
    """An exact id, or an exact name (duplicates and fuzzy names come back as candidates)."""
    tr = ctx["tr"]
    pid = str(args.get("playlist_id") or "").strip()
    if pid:
        return await _m("playlist.get", {"id": pid}, ctx), None
    rows = await _m("playlists.list", {}, ctx)
    name = str(args.get("name") or "").strip().lower()
    if not name:
        items = [{"title": p["name"], "subtitle": tr(f"{p['count']} треков", f"{p['count']} tracks"),
                  "cover": p.get("cover") or "", "command": "playlist", "args": {"name": p["name"]}} for p in rows[:12]]
        return None, {"text": tr("Плейлисты: ", "Playlists: ") + ", ".join(p["name"] for p in rows[:12]),
                      "card": {"kind": "list", "module": "music", "title": tr("Плейлисты", "Playlists"), "items": items}}
    exact = [p for p in rows if str(p.get("name") or "").strip().lower() == name]
    if len(exact) == 1:
        return exact[0], None
    found = exact or [p for p in rows if name in str(p.get("name") or "").lower()][:8]
    if not found:
        raise CommandError(tr(f"Плейлист «{args.get('name')}» не найден", f"Playlist “{args.get('name')}” was not found"))
    lines = "\n".join(f"{i + 1}. {p['name']} (id: {p['id']})" for i, p in enumerate(found))
    return None, {"ok": False, "error": tr("Неоднозначно: уточните плейлист", "Ambiguous: pick a playlist"),
                  "text": tr("Нашлось несколько плейлистов:", "Several playlists match:") + "\n" + lines,
                  "card": {"kind": "list", "module": "music", "title": tr("Уточните плейлист", "Pick a playlist"),
                           "items": [{"title": p["name"], "subtitle": tr(f"{p['count']} треков", f"{p['count']} tracks"),
                                      "cover": p.get("cover") or ""} for p in found]}}

async def _queue_tracks(ctx: Dict[str, Any], ids: List[str], limit: int = 30) -> List[Dict[str, Any]]:
    out = []
    for tid in ids[:limit]:
        try:
            out.append(await _m("library.get", {"id": tid}, ctx))
        except Exception:  # noqa: BLE001 - a track left the library, the rest of the queue still shows
            continue
    return out

async def _apply_queue(ctx: Dict[str, Any], queue: List[str], state: Dict[str, Any]) -> Dict[str, Any]:
    """set_queue restarts the list in both players, so keep the current track and position."""
    current = str(state.get("track_id") or "") or queue[0]
    position = float(state.get("position_now") or 0)
    args = {"queue": queue, "track_id": current, "position": position}
    await _m("player.command", {"cmd": "set_queue", **args, "target": "pc"}, ctx)
    return _boot({"cmd": "set_queue", "args": args, "target": "pc"})

def _playlist_card(pl: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    tr = ctx["tr"]
    tracks = pl.get("tracks") or []
    return {"kind": "list", "module": "music", "title": pl.get("name") or "",
            "subtitle": tr(f"{len(tracks)} треков", f"{len(tracks)} tracks"), "items": _list_items(tracks[:12])}

@handler("music.search")
async def music_search(args, ctx):
    tr = ctx["tr"]
    limit = max(1, min(20, int(args.get("limit") or 10)))
    rows = await _m("library.list", {"q": str(args.get("query") or ""), "limit": limit, "sort": "plays"}, ctx)
    tracks = rows.get("tracks") or []
    if not tracks:
        return {"ok": False, "not_found": True,
                "error": tr(f"В библиотеке нет «{args.get('query')}»", f"“{args.get('query')}” is not in the library")}
    lines = "\n".join(f"{i + 1}. {t.get('title')} — {t.get('artist') or '—'}" for i, t in enumerate(tracks))
    return {"text": tr(f"Нашёл {len(tracks)}:", f"Found {len(tracks)}:") + "\n" + lines,
            "card": {"kind": "list", "module": "music", "title": tr("Результаты поиска", "Search results"),
                     "items": _list_items(tracks)}}

@handler("music.radio")
async def music_radio(args, ctx):
    tr = ctx["tr"]
    seed = None
    if str(args.get("track_id") or "").strip() or str(args.get("query") or "").strip():
        seed, resp = await _resolve_track(args, ctx)
        if resp is not None:
            return resp
    if seed is None:
        state = await _m("player.state", {}, ctx)
        seed = state.get("track") or {}
        if not seed:
            return {"ok": False, "error": tr("Нечего брать за основу: назовите трек", "Nothing to base the radio on: name a track")}
    radio = await _m("library.radio", {"id": seed["id"], "limit": 30}, ctx)
    tracks = radio.get("tracks") or []
    if not tracks:
        return {"ok": False, "error": tr("Похожих треков в библиотеке нет", "No similar tracks in the library")}
    ids = [t["id"] for t in tracks if t.get("id")]
    command = await _play_ids(ids, ids[0], tr(f"Радио: {seed.get('title')}", f"Radio: {seed.get('title')}"), ctx)
    return {"text": tr(f"Радио по треку: {seed.get('title')} — {seed.get('artist') or ''}",
                       f"Radio based on: {seed.get('title')} — {seed.get('artist') or ''}"),
            "card": _track_card(tracks[0], ctx), "client": _boot(command)}

@handler("music.like")
async def music_like(args, ctx):
    tr = ctx["tr"]
    track_id = str(args.get("track_id") or "").strip()
    if track_id:
        track = await _m("library.get", {"id": track_id}, ctx)
    elif str(args.get("query") or "").strip():
        tracks = await _find_tracks(str(args.get("query")), ctx)
        if not tracks:
            return {"ok": False, "error": tr(f"Трек «{args.get('query')}» не найден", f"“{args.get('query')}” was not found")}
        track = tracks[0]
    else:
        state = await _m("player.state", {}, ctx)
        track = state.get("track") or {}
        if not track:
            return {"ok": False, "error": tr("Сейчас ничего не играет", "Nothing is playing")}
    liked = bool(args["liked"]) if "liked" in args else True
    await _m("library.like", {"id": track["id"], "liked": liked}, ctx)
    track = await _m("library.get", {"id": track["id"]}, ctx)
    state = await _m("player.state", {}, ctx)
    playing = bool(state.get("playing")) and not state.get("stale")
    return {"text": tr(f"{'Лайк: ' if liked else 'Лайк убран: '}{track.get('title')} — {track.get('artist') or ''}",
                       f"{'Liked: ' if liked else 'Unliked: '}{track.get('title')} — {track.get('artist') or ''}"),
            "card": _track_card(track, ctx, playing=playing, state=state)}

@handler("music.delete_track")
async def music_delete_track(args, ctx):
    tr = ctx["tr"]
    track_id = str(args.get("track_id") or "").strip()
    if not track_id:
        query = str(args.get("query") or "").strip()
        if not query:
            raise CommandError(tr("Нужен id трека", "A track id is required"))
        tracks = await _find_tracks(query, ctx, limit=8)
        if not tracks:
            raise CommandError(tr(f"Трек «{query}» не найден", f"“{query}” was not found"))
        return _candidates(tracks, ctx, "Какой трек удалить? Удаление по названию не выполняется:",
                           "Which track should be deleted? A name alone never deletes:")
    track = await _m("library.get", {"id": track_id}, ctx)
    # library.delete defaults to removing the file, so always say so explicitly: no flag, no file removal.
    delete_file = bool(args.get("delete_file") or False)
    result = await _m("library.delete", {"id": track_id, "delete_file": delete_file}, ctx)
    removed = tr("файл тоже удалён", "the file was removed too") if result.get("file_removed") else \
        tr("файл на диске остался", "the file stays on disk") if delete_file else tr("только запись в библиотеке", "only the library entry")
    return {"text": tr(f"Трек удалён: {track.get('title')} — {track.get('artist') or ''} ({removed})",
                       f"Track deleted: {track.get('title')} — {track.get('artist') or ''} ({removed})"),
            "card": {"kind": "info", "module": "music", "title": tr("Трек удалён", "Track deleted"),
                     "subtitle": f"{track.get('title')} — {track.get('artist') or ''}",
                     "actions": [{"icon": "open", "label": tr("Открыть MemuMusic", "Open MemuMusic"), "open": True}]}}

@handler("music.seek")
async def music_seek(args, ctx):
    position = max(0, int(args.get("position") or 0))
    return await _simple("seek", ctx, f"Перемотано на {position} с", f"Seeked to {position}s", position=position)

@handler("music.shuffle")
async def music_shuffle(args, ctx):
    if "enabled" not in args:
        return await _simple("shuffle", ctx, "Перемешивание переключено", "Shuffle toggled")
    enabled = bool(args["enabled"])
    return await _simple("shuffle", ctx, "Перемешивание включено" if enabled else "Перемешивание выключено",
                         "Shuffle on" if enabled else "Shuffle off", value=enabled)

@handler("music.repeat")
async def music_repeat(args, ctx):
    mode = str(args.get("mode") or "off")
    label = {"off": ("выключен", "off"), "all": ("все треки", "all"), "one": ("один трек", "one")}[mode]
    return await _simple("repeat", ctx, f"Повтор: {label[0]}", f"Repeat: {label[1]}", value=mode)

@handler("music.queue")
async def music_queue(args, ctx):
    tr = ctx["tr"]
    action = str(args.get("action") or "")
    if not action:
        action = "move" if args.get("position") is not None else \
            "add" if (args.get("track_id") or args.get("query")) else "show"
    if action not in ("show", "add", "remove", "move"):
        raise CommandError(tr("Действие: show, add, remove или move", "Action: show, add, remove or move"))
    state = await _m("player.state", {}, ctx)
    queue = [str(x) for x in state.get("queue") or [] if x]
    current = str(state.get("track_id") or "")
    if action == "show":
        tracks = await _queue_tracks(ctx, queue or ([current] if current else []))
        if not tracks:
            return {"text": tr("Очередь пуста", "The queue is empty")}
        lines = "\n".join(f"{i + 1}. {t.get('title')} — {t.get('artist') or '—'}" for i, t in enumerate(tracks))
        return {"text": tr(f"Очередь ({len(queue)}):", f"Queue ({len(queue)}):") + "\n" + lines,
                "card": {"kind": "list", "module": "music", "title": tr("Очередь", "Queue"), "items": _list_items(tracks)}}
    track, resp = await _resolve_track(args, ctx)
    if resp is not None:
        return resp
    tid = str(track["id"])
    if action == "add":
        if tid in queue:
            return {"text": tr(f"«{track.get('title')}» уже в очереди", f"“{track.get('title')}” is already in the queue")}
        queue = queue + [tid]
        verb = tr("добавлен в очередь", "added to the queue")
    elif action == "remove":
        if tid not in queue:
            return {"ok": False, "error": tr("Этого трека нет в очереди", "That track is not in the queue")}
        queue = [x for x in queue if x != tid]
        verb = tr("убран из очереди", "removed from the queue")
    else:
        if tid not in queue:
            return {"ok": False, "error": tr("Этого трека нет в очереди", "That track is not in the queue")}
        queue.remove(tid)
        at = max(0, min(len(queue), int(args.get("position") or 1) - 1))
        queue.insert(at, tid)
        verb = tr(f"переставлен на {at + 1} место", f"moved to position {at + 1}")
    if not queue:
        await _m("player.command", {"cmd": "stop", "target": "pc"}, ctx)
        return {"text": tr("Очередь пуста, воспроизведение остановлено", "The queue is empty, playback stopped")}
    client = await _apply_queue(ctx, queue, state)
    tracks = await _queue_tracks(ctx, queue)
    return {"text": tr(f"«{track.get('title')}» {verb}. В очереди {len(queue)}.",
                       f"“{track.get('title')}” {verb}. {len(queue)} in the queue."),
            "card": {"kind": "list", "module": "music", "title": tr("Очередь", "Queue"), "items": _list_items(tracks)},
            "client": client}

@handler("music.rename_track")
async def music_rename_track(args, ctx):
    tr = ctx["tr"]
    changes = {key: args[key] for key in ("title", "artist", "album") if key in args}
    if not changes:
        raise CommandError(tr("Укажите новое название, исполнителя или альбом", "Give a new title, artist or album"))
    track = await _m("library.update", {"id": str(args.get("track_id") or ""), **changes}, ctx)
    return {"text": tr(f"Переименовано: {track.get('title')} — {track.get('artist') or ''}",
                       f"Renamed: {track.get('title')} — {track.get('artist') or ''}"),
            "card": _track_card(track, ctx, playing=False)}

@handler("music.playlist_create")
async def music_playlist_create(args, ctx):
    tr = ctx["tr"]
    ids: List[str] = []
    if str(args.get("query") or "").strip():
        tracks = await _find_tracks(str(args.get("query")), ctx)
        if not tracks:
            return {"ok": False, "error": tr(f"По запросу «{args.get('query')}» ничего не нашлось",
                                             f"Nothing matched “{args.get('query')}”")}
        ids = [t["id"] for t in tracks[:50]]
    pl = await _m("playlist.create", {"name": str(args.get("name") or ""), "description": str(args.get("description") or ""),
                                      "track_ids": ids}, ctx)
    return {"text": tr(f"Плейлист «{pl.get('name')}» создан ({len(pl.get('tracks') or ids)} треков)",
                       f"Playlist “{pl.get('name')}” created ({len(pl.get('tracks') or ids)} tracks)"),
            "card": _playlist_card(pl, ctx)}

@handler("music.playlist_delete")
async def music_playlist_delete(args, ctx):
    tr = ctx["tr"]
    pl, resp = await _resolve_playlist(args, ctx)
    if resp is not None:
        return resp
    name = pl.get("name") or ""
    await _m("playlist.delete", {"id": pl["id"]}, ctx)
    return {"text": tr(f"Плейлист «{name}» удалён", f"Playlist “{name}” deleted"),
            "card": {"kind": "info", "module": "music", "title": tr("Плейлист удалён", "Playlist deleted"), "subtitle": name,
                     "actions": [{"icon": "open", "label": tr("Открыть MemuMusic", "Open MemuMusic"), "open": True}]}}

async def _playlist_edit(args, ctx, kind: str) -> Dict[str, Any]:
    """playlist.add / remove: the same resolution, one library call each."""
    tr = ctx["tr"]
    pl, resp = await _resolve_playlist(args, ctx)
    if resp is not None:
        return resp
    track, resp = await _resolve_track(args, ctx)
    if resp is not None:
        return resp
    updated = await _m("playlist.update", {"id": pl["id"], kind: [track["id"]]}, ctx)
    verb = tr("добавлен в плейлист", "added to the playlist") if kind == "add" else tr("убран из плейлиста", "removed from the playlist")
    return {"text": tr(f"«{track.get('title')}» {verb} «{updated.get('name')}»",
                       f"“{track.get('title')}” {verb} “{updated.get('name')}”"),
            "card": _playlist_card(updated, ctx)}

@handler("music.playlist_add")
async def music_playlist_add(args, ctx):
    return await _playlist_edit(args, ctx, "add")

@handler("music.playlist_remove")
async def music_playlist_remove(args, ctx):
    return await _playlist_edit(args, ctx, "remove")

@handler("music.playlist_reorder")
async def music_playlist_reorder(args, ctx):
    tr = ctx["tr"]
    pl, resp = await _resolve_playlist(args, ctx)
    if resp is not None:
        return resp
    track, resp = await _resolve_track(args, ctx)
    if resp is not None:
        return resp
    to = max(0, int(args.get("position") or 1) - 1)
    updated = await _m("playlist.reorder", {"id": pl["id"], "ids": [track["id"]], "to": to}, ctx)
    return {"text": tr(f"«{track.get('title')}» переставлен на {to + 1} место в плейлисте «{updated.get('name')}»",
                       f"“{track.get('title')}” moved to position {to + 1} in “{updated.get('name')}”"),
            "card": _playlist_card(updated, ctx)}

@handler("music.settings")
async def music_settings(args, ctx):
    tr = ctx["tr"]
    key = str(args.get("key") or "")
    if key not in _SAFE_SETTINGS:
        raise CommandError(tr(f"Настройка «{key}» недоступна для ИИ", f"The setting “{key}” is not available to the AI"))
    current = await _m("settings.get", {}, ctx)
    if "value" not in args:
        return {"text": tr(f"{key}: {current.get(key)}", f"{key}: {current.get(key)}"),
                "card": {"kind": "info", "module": "music", "title": key, "subtitle": str(current.get(key))}}
    raw = str(args.get("value") or "")
    old = current.get(key)
    if isinstance(old, bool):
        value: Any = raw.strip().lower() in ("1", "true", "yes", "on", "да", "вкл", "включено")
    elif isinstance(old, (int, float)):
        try:
            value = float(raw)
        except ValueError:
            raise CommandError(tr(f"{key}: ожидается число", f"{key}: a number is expected"))
        if key == "volume" and value > 1:
            value /= 100  # the library keeps volume 0..1; «40» from a user means 40%
        value = int(value) if isinstance(old, int) else value
    else:
        value = raw
    updated = await _m("settings.set", {key: value}, ctx)
    value = updated.get(key, value)
    return {"text": tr(f"{key}: {value}", f"{key}: {value}"),
            "card": {"kind": "info", "module": "music", "title": key, "subtitle": str(value)}}

@handler("music.toggle")
async def music_toggle(args, ctx):
    return await _simple("toggle", ctx, "Переключено", "Toggled")

@handler("music.stop")
async def music_stop(args, ctx):
    return await _simple("stop", ctx, "Воспроизведение остановлено", "Playback stopped")

@handler("music.duplicates")
async def music_duplicates(args, ctx):
    tr = ctx["tr"]
    groups = await _m("library.duplicates", {}, ctx) or []
    if not groups:
        return {"text": tr("Дубликатов в библиотеке не нашлось", "No duplicates in the library")}
    lines = []
    for group in groups[:12]:
        tracks = group.get("tracks") or []
        first = tracks[0] if tracks else {}
        lines.append(tr(f"{first.get('title')} — {first.get('artist') or '—'}: {len(tracks)} копий",
                        f"{first.get('title')} — {first.get('artist') or '—'}: {len(tracks)} copies"))
    return {"text": tr(f"Дубликаты: {len(groups)} групп", f"Duplicates: {len(groups)} groups") + "\n" + "\n".join(lines)}

@handler("music.stats")
async def music_stats(args, ctx):
    tr = ctx["tr"]
    stats = await _m("library.stats", {"days": 30}, ctx) or {}
    return {"text": tr(f"В библиотеке {stats.get('tracks', 0)} треков, {stats.get('artists', 0)} исполнителей",
                       f"The library has {stats.get('tracks', 0)} tracks by {stats.get('artists', 0)} artists")}

@handler("characters.open")
async def characters_open(args, ctx):
    tr = ctx["tr"]
    from .characters_api import store_get
    index = await asyncio.to_thread(store_get, ctx["root"], "characters", "index", None) or []
    name = str(args.get("name") or "").strip().lower()
    rows = [r for r in index if isinstance(r, dict) and r.get("id")]
    if not rows:
        return {"ok": False, "error": tr("В «Персонажах» пока нет персонажей", "There are no characters yet")}
    if name:
        best = max(rows, key=lambda r: difflib.SequenceMatcher(None, name, str(r.get("name") or "").lower()).ratio()
                   + (0.6 if name in str(r.get("name") or "").lower() else 0))
        if difflib.SequenceMatcher(None, name, str(best.get("name") or "").lower()).ratio() < 0.4 and name not in str(best.get("name") or "").lower():
            return {"ok": False, "error": tr(f"Персонаж «{args.get('name')}» не найден", f"Character “{args.get('name')}” was not found")}
    else:
        best = max(rows, key=lambda r: r.get("updated") or 0)
    return {"text": tr(f"Открываю чат с {best.get('name')}", f"Opening the chat with {best.get('name')}"),
            "card": {"kind": "info", "module": "characters", "title": str(best.get("name") or ""),
                     "subtitle": tr("Чат открывается в «Персонажах»", "The chat opens in Characters"),
                     "actions": [{"icon": "open", "label": tr("Открыть чат", "Open the chat"), "open": True,
                                  "detail": {"query": str(best["id"])}}],
                     "cover": best.get("thumb") if str(best.get("thumb") or "").startswith("data:image/") else ""},
            "client": {"open_module": "characters", "detail": {"query": str(best["id"]), "from": "command"}}}

def _monitor_rows(module_id: str, limit: int) -> List[Dict[str, Any]]:
    from . import module_monitor
    events = module_monitor.events(0, module_id).get("events") or []
    return list(reversed(events))[:limit]

def _money(value: Any, en: bool) -> str:
    if value is None:
        return ""
    if not value:
        return "free" if en else "бесплатно"
    return f"${value:.3f}".rstrip("0").rstrip(".") if value < 1 else f"${value:.2f}".rstrip("0").rstrip(".")

@handler("api-models.recent")
async def api_models_recent(args, ctx):
    tr, en = ctx["tr"], ctx["en"]
    from .module_integration_data import api_models_digest
    digest = await asyncio.to_thread(api_models_digest, int(args.get("limit") or 8))
    models = digest["models"]
    if not models:
        return {"text": tr("Новых моделей пока не было (или наблюдение выключено в модуле «Новые модели API»)",
                           "No new models yet (or monitoring is off in the New API models module)"),
                "card": {"kind": "info", "module": "api-models", "title": tr("Пока тихо", "All quiet so far"),
                         "subtitle": tr("Новые модели появятся, когда их добавят в API", "New models appear once an API lists them"),
                         "actions": [{"icon": "open", "label": tr("Открыть модуль", "Open the module"), "open": True}]}}
    items = []
    for m in models:
        price = ""
        if m.get("input") is not None or m.get("output") is not None:
            price = (_money(m.get("input"), en) or "—") + " → " + (_money(m.get("output"), en) or "—")
        outputs = {"text": tr("текст", "text"), "image": tr("изображения", "images"), "audio": tr("аудио", "audio"), "video": tr("видео", "video")}
        output = ", ".join(outputs.get(value, value) for value in m.get("outputs", []))
        meta = " · ".join(x for x in (m.get("family") or m["source"], output, price and tr(f"{price} за 1M", f"{price} per 1M"),
                                      m.get("context") and tr(f"контекст {m['context'] // 1000}K", f"{m['context'] // 1000}K context")) if x)
        items.append({"title": m["name"], "subtitle": meta, "at": m["at"], "mono": (m.get("family") or m["source"] or "?")[:1].upper(),
                      "open": True, "detail": {"query": m["key"]}})
    return {"text": "\n".join(f"- {i['title']} ({i['subtitle']})" for i in items),
            "card": {"kind": "list", "module": "api-models", "title": tr("Новые модели API", "New API models"), "items": items,
                     "actions": [{"icon": "open", "label": tr("Открыть ленту", "Open the feed"), "open": True}]}}

@handler("benchmarks.leaders")
async def benchmarks_leaders(args, ctx):
    tr, en = ctx["tr"], ctx["en"]
    from .module_integration_data import benchmarks_digest, find_board
    digest = await asyncio.to_thread(benchmarks_digest, ctx["root"], int(args.get("top") or 5))
    board = find_board(digest, str(args.get("board") or ""))
    if board is None:
        return {"ok": False, "error": tr("Рейтинги ещё не загружены: откройте модуль «Бенчмарки» один раз",
                                         "No leaderboards yet: open the Benchmarks module once"),
                "card": {"kind": "info", "module": "benchmarks", "title": tr("Рейтинги не загружены", "No leaderboards yet"),
                         "actions": [{"icon": "open", "label": tr("Открыть «Бенчмарки»", "Open Benchmarks"), "open": True}]}}
    title = board["title_en"] if en else board["title"]
    items = [{"title": r["model"], "subtitle": r["org"], "rank": i + 1, "value": r["score"], "open": True, "detail": {"query": r["model"]}}
             for i, r in enumerate(board["rows"])]
    others = ", ".join((b["title_en"] if en else b["title"]) for b in digest["boards"] if b["id"] != board["id"])
    return {"text": f"{title}: " + "; ".join(f"{i['rank']}. {i['title']} ({i['value']})" for i in items)
                    + (tr(f". Другие рейтинги: {others}", f". Other boards: {others}") if others else ""),
            "card": {"kind": "list", "module": "benchmarks", "title": title, "subtitle": tr("Лидеры", "Leaders"), "items": items,
                     "actions": [{"icon": "open", "label": tr("Открыть «Бенчмарки»", "Open Benchmarks"), "open": True}]}}

@handler("benchmarks.changes")
async def benchmarks_changes(args, ctx):
    tr = ctx["tr"]
    rows = await asyncio.to_thread(_monitor_rows, "benchmarks", int(args.get("limit") or 8))
    hint = tr("Таблицы лидеров — в модуле «Бенчмарки»: откройте его, чтобы сравнить модели.",
              "Leaderboards live in the Benchmarks module: open it to compare models.")
    items = [{"title": r.get("title") or "", "subtitle": str(r.get("text") or "")[:160], "at": r.get("at")} for r in rows]
    return {"text": ("\n".join(f"- {i['title']}: {i['subtitle']}" for i in items) + "\n" if items else "") + hint,
            "card": {"kind": "list", "module": "benchmarks", "title": tr("Изменения бенчмарков", "Benchmark changes"), "items": items,
                     "subtitle": "" if items else hint,
                     "actions": [{"icon": "open", "label": tr("Открыть «Бенчмарки»", "Open Benchmarks"), "open": True,
                                  "detail": {"query": str(args.get("topic") or "")}}]},
            "client": {"open_module": "benchmarks", "detail": {"query": str(args.get("topic") or ""), "from": "command"}}
            if args.get("open") else {}}

@handler("learn.due")
async def learn_due(args, ctx):
    """r40-learn: «что повторить сегодня» — MemuLearn cards due today per course and the minutes it takes."""
    tr, en = ctx["tr"], ctx["en"]
    from .learn.api import panel_digest
    d = await asyncio.to_thread(panel_digest, ctx["lang"])
    total, minutes = d["due"], d["minutes"]
    items = [{"title": c["title"], "subtitle": tr(f"к повторению: {c['due']}, новых: {c['new']}", f"due: {c['due']}, new: {c['new']}"),
              "value": c["due"], "open": True, "detail": {"course": c["id"]}} for c in d["courses"]]
    head = (tr(f"Сегодня к повторению {total} карт. (~{max(1, minutes)} мин)", f"{total} cards due today (~{max(1, minutes)} min)")
            if total else tr("Сегодня повторять нечего", "Nothing is due today"))
    if not d["courses"]:
        head = tr("В MemuLearn пока нет курсов", "There are no MemuLearn courses yet")
    return {"text": head + ("\n" + "\n".join(f"- {i['title']}: {i['subtitle']}" for i in items) if items else ""),
            "card": {"kind": "list", "module": "learn", "title": tr("Повторить сегодня", "Review today"), "subtitle": head, "items": items,
                     "actions": [{"icon": "open", "label": tr("Начать повторение", "Start reviewing"), "open": True, "detail": {"route": "review"}}]
                     if total else [{"icon": "open", "label": tr("Открыть MemuLearn", "Open MemuLearn"), "open": True}]}}

from .utils_kit import chat as _utils_chat  # noqa: E402,F401  (MemuUtils: registers utils.* handlers and the live skill)
