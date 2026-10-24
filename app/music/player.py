# -*- coding: utf-8 -*-
"""Host player state on the server: what plays where, plus a command mailbox (remote control).

The player that actually plays (MemuAI window or the phone) reports itself with ``player.update``
(on track change, play/pause, seek and about every 15 s — no fast polling). A remote (Mini App, the
main window mini-player) sends ``player.command``; the playing side receives commands through
``GET /api/pg/music/player/events`` (SSE) or the long-poll ``player.wait`` and applies them.
The current position is extrapolated on read, so a remote shows a moving progress bar without polling.
"""
from __future__ import annotations

import asyncio
import collections
import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

COMMANDS = ("play", "pause", "toggle", "next", "prev", "seek", "play_track", "set_queue", "volume", "shuffle",
            "repeat", "stop", "handoff")
_STATE_KEYS = {"track_id": str, "position": float, "playing": bool, "queue": list, "index": int, "volume": float,
               "shuffle": bool, "repeat": str, "device": str, "device_name": str, "duration": float, "context": dict,
               "speed": float}   # r39: playback rate — the extrapolated position runs at this speed


STALE_SECONDS = 120.0   # the playing side reports every ~15 s; silence this long means the window/phone is gone
PENDING_SECONDS = 2.5   # r40: a remote play / pause not confirmed by the player's report within this time is rolled back
_PC_TARGETS = ("pc", "desktop", "memuai", "dock")


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off", "null", "none")
    return bool(value)


_CAST = {bool: _as_bool}


class PlayerHub:
    def __init__(self, path: Optional[Path] = None):
        self.path = path
        self.cond = threading.Condition()
        self.version = 0
        self.cmd_seq = 0
        self.commands: "collections.deque[Dict[str, Any]]" = collections.deque(maxlen=50)
        # Round 39 (r39-integ): async listeners (SSE, long-poll) wait on an asyncio.Event, not in a worker thread —
        # each to_thread(wait) held a default-executor thread for 15–25 s (and 15 s more after the client left), so a few
        # open players starved the pool and every command / state read queued behind them for seconds.
        self._waiters: set = set()
        self.state: Dict[str, Any] = {"track_id": "", "position": 0.0, "position_at": 0.0, "playing": False,
                                      "queue": [], "index": -1, "volume": 1.0, "shuffle": False, "repeat": "off",
                                      "device": "", "device_name": "", "duration": 0.0, "context": {}, "speed": 1.0,
                                      "updated_at": 0.0, "pending": None}
        self._load()

    def _load(self) -> None:
        if not self.path:
            return
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for key, kind in _STATE_KEYS.items():
            if key in saved:
                try:
                    self.state[key] = _CAST.get(kind, kind)(saved[key])
                except (TypeError, ValueError):
                    pass
        self.state["playing"] = False   # after a restart nothing plays

    def _save(self) -> None:
        if not self.path:
            return
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({k: self.state.get(k) for k in _STATE_KEYS}, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def _position_now(self, now: float) -> float:
        state = self.state
        pos = float(state["position"] or 0)
        if state["playing"] and state["position_at"]:
            pos += max(0.0, now - state["position_at"]) * float(state.get("speed") or 1.0)
            if state["duration"]:
                pos = min(pos, state["duration"])
        return pos

    def _expire(self, now: float) -> None:
        """A player that stopped reporting (window closed, phone asleep) no longer «plays»."""
        if self.state["playing"] and self.state["updated_at"] and now - self.state["updated_at"] > STALE_SECONDS:
            self.state["position"] = self._position_now(self.state["updated_at"] + STALE_SECONDS)
            self.state["position_at"] = now
            self.state["playing"] = False
            self.state["stale"] = True

    def snapshot(self) -> Dict[str, Any]:
        with self.cond:   # an RLock underneath: update()/wait() call this while holding it
            now = time.time()
            self._expire(now)
            self._rollback(now)
            state = dict(self.state)
            state["position_now"] = round(self._position_now(now), 2)
        state.setdefault("stale", False)
        state["pending"] = bool(state.get("pending"))
        state["online"] = bool(state["updated_at"]) and now - state["updated_at"] <= STALE_SECONDS
        state["version"] = self.version
        state["cmd_seq"] = self.cmd_seq
        state["server_time"] = time.time()
        return state

    def update(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        with self.cond:
            now = time.time()
            self._expire(now)
            # Out-of-order reports (two bridge calls racing): a report stamped older than the last applied one from the
            # same device is ignored. ``at`` — the client's clock, seconds or milliseconds since the epoch.
            try:
                at = float(fields.get("at") or 0)
            except (TypeError, ValueError):
                at = 0.0
            if at > 1e11:
                at /= 1000.0
            device = str(fields.get("device") or self.state.get("device") or "")
            if at and device == self.state.get("device") and at < float(self.state.get("client_at") or 0):
                return {**self.snapshot(), "ignored": True}
            if at:
                self.state["client_at"] = at
            if fields.get("position") is None:
                # Play/pause or a track change without a position: freeze the extrapolated position first, otherwise
                # resuming after a long pause would jump forward by the whole pause.
                same_track = fields.get("track_id") in (None, self.state["track_id"])
                self.state["position"] = self._position_now(now) if same_track else 0.0
                self.state["position_at"] = now
            self.state["stale"] = False
            self.state["pending"] = None   # the playing side reported: its word is the truth
            for key, kind in _STATE_KEYS.items():
                if key in fields and fields[key] is not None:
                    try:
                        value = _CAST.get(kind, kind)(fields[key])
                    except (TypeError, ValueError):
                        continue
                    if key == "queue":
                        value = [str(x) for x in value][:5000]
                    if key == "volume":
                        value = max(0.0, min(1.0, value))
                    if key == "speed":
                        value = max(0.25, min(4.0, value))
                    self.state[key] = value
            if fields.get("position") is not None:
                self.state["position_at"] = now
            self.state["updated_at"] = now
            self.version += 1
            self._save()
            self.cond.notify_all()
            self._wake()
            return self.snapshot()

    def command(self, cmd: str, args: Dict[str, Any], sender: str = "") -> Dict[str, Any]:
        if cmd not in COMMANDS:
            raise ValueError("Неизвестная команда плеера / Unknown player command")
        with self.cond:
            self.cmd_seq += 1
            item = {"seq": self.cmd_seq, "cmd": cmd, "args": {k: v for k, v in (args or {}).items()
                                                               if k not in ("cmd", "op")},
                    "from": str(sender or ""), "target": str((args or {}).get("target") or ""), "at": time.time()}
            self.commands.append(item)
            self._optimistic(cmd, item)
            self.version += 1
            self.cond.notify_all()
            self._wake()
            return {"ok": True, "seq": self.cmd_seq}

    # Round 40 (r40-music-fix): one player state, instantly everywhere. A remote play / pause / toggle / stop used to
    # bump the version with the OLD state — every widget (the dock, Quick Access, the chat) got «still playing» back,
    # the one that switched its button optimistically flipped back, and only the player's own report (0.5–1 s later)
    # set it right: «the song stopped, the button changes a second later». Now the command itself moves the shared
    # state (``pending``) when it is addressed to the device that plays and that device is online; its report confirms
    # it, and without a report within PENDING_SECONDS the state rolls back to what the player last said.
    def _optimistic(self, cmd: str, item: Dict[str, Any]) -> None:
        if cmd not in ("play", "pause", "toggle", "stop") or not self.state.get("track_id"):
            return
        now = time.time()
        state = self.state
        if not state.get("updated_at") or now - state["updated_at"] > STALE_SECONDS or state.get("stale"):
            return
        target = item["target"].lower()
        device = str(state.get("device") or "pc")
        if target and not (target == device or (device == "pc" and target in _PC_TARGETS)):
            return
        prev = state.get("pending") or {}
        was = prev.get("was", state["playing"]) if prev else state["playing"]
        playing = (not state["playing"]) if cmd == "toggle" else cmd == "play"
        if playing == state["playing"] and not prev:
            return
        state["position"] = self._position_now(now)
        state["position_at"] = now
        state["playing"] = playing
        state["pending"] = {"seq": item["seq"], "was": was, "until": now + PENDING_SECONDS}

    def _rollback(self, now: float) -> None:
        pending = self.state.get("pending")
        if pending and now > float(pending.get("until") or 0):
            self.state["position"] = self._position_now(now)
            self.state["position_at"] = now
            self.state["playing"] = bool(pending.get("was"))
            self.state["pending"] = None
            self.version += 1

    def _until_rollback(self, timeout: float) -> float:
        """A listener wakes up when an unconfirmed command rolls back, so every widget sees the rollback too."""
        pending = self.state.get("pending")
        return min(timeout, max(0.0, float(pending["until"]) - time.time() + 0.02)) if pending else timeout

    def pending(self, since_cmd: int) -> List[Dict[str, Any]]:
        return [c for c in self.commands if c["seq"] > since_cmd and time.time() - c["at"] < 120]

    def wait(self, since: int, since_cmd: int = -1, timeout: float = 25.0) -> Dict[str, Any]:
        """Block until something changed after ``since`` (or the timeout) — the long-poll."""
        timeout = max(0.0, min(25.0, float(timeout)))
        with self.cond:
            timeout = self._until_rollback(timeout)
            if self.version <= since:
                self.cond.wait_for(lambda: self.version > since, timeout=timeout)
            cmds = self.pending(since_cmd if since_cmd >= 0 else self.cmd_seq)
            return {"state": self.snapshot(), "commands": cmds, "changed": self.version > since}


    def _wake(self) -> None:
        for loop, event in list(self._waiters):
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:   # the loop is closed
                self._waiters.discard((loop, event))

    async def wait_async(self, since: int, since_cmd: int = -1, timeout: float = 25.0) -> Dict[str, Any]:
        """``wait`` for the event loop: no thread is held while nothing changes."""
        timeout = max(0.0, min(25.0, float(timeout)))
        entry = (asyncio.get_running_loop(), asyncio.Event())
        with self.cond:
            timeout = self._until_rollback(timeout)
            if self.version > since:
                entry[1].set()
            else:
                self._waiters.add(entry)
        try:
            await asyncio.wait_for(entry[1].wait(), timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            with self.cond:
                self._waiters.discard(entry)
        with self.cond:
            cmds = self.pending(since_cmd if since_cmd >= 0 else self.cmd_seq)
            return {"state": self.snapshot(), "commands": cmds, "changed": self.version > since}


_HUBS: Dict[str, PlayerHub] = {}
_LOCK = threading.Lock()


def hub(root: Path) -> PlayerHub:
    key = str(root)
    with _LOCK:
        if key not in _HUBS:
            _HUBS[key] = PlayerHub(Path(root) / "player.json")
        return _HUBS[key]
