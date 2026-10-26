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
        meta = " · ".join(x for x in (m.get("family") or m["source"], price and tr(f"{price} за 1M", f"{price} per 1M"),
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
