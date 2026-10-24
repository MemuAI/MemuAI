"""Timers, alarms, local reminders, stopwatch and Pomodoro for Quick Ask (Round 22).

One :class:`TimerManager` per MemuAI window process. State lives in
``memuai/assistant_timers.json`` with *absolute* end times, so timers and
alarms survive a restart of MemuAI (an alarm that came due while MemuAI was
closed rings at the next start, marked ``late``). ``tick(now)`` is pure state
logic (tested with a fake clock); the background thread only calls it and
hands fired events to ``on_fire`` (sound + Windows notification + optional
pop-up of the Quick Ask window).
"""
from __future__ import annotations

import io
import json
import logging
import math
import os
import struct
import sys
import threading
import time
import uuid
import wave
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("memuai.assistant.timers")

MAX_TIMERS = 30
RING_SECONDS = 60          # a ring stops by itself after a minute
POMODORO_DEFAULTS = {"work": 25, "short_break": 5, "long_break": 15, "cycles": 4, "auto_breaks": True, "auto_work": False}


def _id() -> str:
    return uuid.uuid4().hex[:8]


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _valid_timer(row: Any) -> bool:
    """A persisted timer the ticking thread can handle (Round 23: one broken row — a missing or
    non-numeric ``ends_at`` — used to make every tick raise, so no timer ever rang again)."""
    if not isinstance(row, dict) or not row.get("id") or _finite(row.get("ends_at")) is None:
        return False
    paused = row.get("paused_remaining")
    if paused is not None and _finite(paused) is None:
        return False
    if row.get("kind", "timer") not in ("timer", "alarm", "reminder"):
        return False
    return True


def empty_state() -> Dict[str, Any]:
    return {"timers": [], "stopwatch": {"running": False, "started_at": 0.0, "elapsed": 0.0, "laps": []},
            "pomodoro": {"active": False}, "ringing": [], "stats": {}}


class TimerManager:
    def __init__(self, path: Optional[Path] = None, *, clock: Callable[[], float] = time.time,
                 on_fire: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
        self.path = Path(path) if path else None
        self.clock = clock
        self.on_fire = on_fire
        self.lock = threading.RLock()
        self.state = empty_state()
        self.version = 0
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.load()

    # ------------------------------------------------------------ persistence
    def load(self) -> None:
        if not self.path:
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            state = empty_state()
            for key in state:
                if isinstance(data.get(key), type(state[key])):
                    state[key] = data[key]
            state["timers"] = [t for t in state["timers"] if _valid_timer(t)][:MAX_TIMERS]
            state["ringing"] = [r for r in state["ringing"] if isinstance(r, dict) and r.get("id")][-10:]
            pomo = state["pomodoro"]
            if pomo.get("active") and (_finite(pomo.get("ends_at")) is None or not isinstance(pomo.get("phase"), str)):
                state["pomodoro"] = {"active": False}
            sw = state["stopwatch"]
            if _finite(sw.get("elapsed")) is None or _finite(sw.get("started_at")) is None:
                state["stopwatch"] = empty_state()["stopwatch"]
            self.state = state

    def save(self) -> None:
        self.version += 1
        self._wake.set()
        if not self.path:
            return
        try:
            from .storage import save_json
            save_json(str(self.path), self.state, backup=False)
        except Exception as exc:
            log.debug("timers not saved: %s", exc)

    # ------------------------------------------------------------ timers / alarms
    def add_timer(self, seconds: float, label: str = "", *, kind: str = "timer", text: str = "",
                  ends_at: Optional[float] = None, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        now = self.clock()
        with self.lock:
            if len([t for t in self.state["timers"] if not t.get("done")]) >= MAX_TIMERS:
                raise ValueError("too many timers")
            end = _finite(ends_at) if ends_at is not None else (
                now + max(1.0, _finite(seconds)) if _finite(seconds) is not None else None)
            if end is None or (ends_at is None and end - now > 400 * 86400):
                raise ValueError("timer time must be a finite moment within 400 days")
            timer = {"id": _id(), "kind": kind, "label": str(label or "")[:60], "text": str(text or "")[:500],
                     "duration": round(max(0.0, end - now), 1) if kind == "timer" else round(max(0.0, end - now), 1),
                     "created": now, "ends_at": end, "paused_remaining": None}
            if meta:
                timer["meta"] = meta
            self.state["timers"].append(timer)
            self.save()
            return dict(timer)

    def find(self, ref: str = "", kind: Optional[str] = None) -> List[Dict[str, Any]]:
        ref = str(ref or "").strip().lower()
        rows = [t for t in self.state["timers"] if kind is None or t.get("kind") == kind]
        if not ref:
            return rows
        exact = [t for t in rows if t["id"] == ref or t.get("label", "").lower() == ref]
        if exact:
            return exact
        return [t for t in rows if ref in t.get("label", "").lower() or ref in t.get("text", "").lower()]

    def cancel(self, ref: str = "", *, all_: bool = False, kind: Optional[str] = "timer") -> List[Dict[str, Any]]:
        with self.lock:
            if all_:
                victims = [t for t in self.state["timers"] if kind is None or t.get("kind") == kind]
            else:
                found = self.find(ref, kind)
                if not ref and len(found) > 1:
                    # «отмени таймер» with several: the one that ends soonest
                    found = sorted(found, key=lambda t: self.remaining(t))[:1]
                victims = found
            ids = {t["id"] for t in victims}
            self.state["timers"] = [t for t in self.state["timers"] if t["id"] not in ids]
            self.state["ringing"] = [r for r in self.state["ringing"] if r.get("id") not in ids]
            if victims:
                self.save()
            return victims

    def restore(self, timer: Dict[str, Any]) -> Dict[str, Any]:
        """Undo a cancel: put the timer back with its end time."""
        with self.lock:
            if any(t["id"] == timer.get("id") for t in self.state["timers"]):
                return timer
            self.state["timers"].append(dict(timer))
            self.save()
            return timer

    def pause(self, ref: str = "", resume: bool = False) -> List[Dict[str, Any]]:
        now = self.clock()
        with self.lock:
            rows = self.find(ref, "timer") or ([] if ref else self.state["timers"])
            changed = []
            for t in rows:
                if t.get("kind") != "timer":
                    continue
                if resume and t.get("paused_remaining") is not None:
                    t["ends_at"] = now + float(t["paused_remaining"])
                    t["paused_remaining"] = None
                    changed.append(t)
                elif not resume and t.get("paused_remaining") is None:
                    t["paused_remaining"] = max(0.0, float(t["ends_at"]) - now)
                    changed.append(t)
            if changed:
                self.save()
            return [dict(t) for t in changed]

    def remaining(self, timer: Dict[str, Any], now: Optional[float] = None) -> float:
        if timer.get("paused_remaining") is not None:
            return float(timer["paused_remaining"])
        return max(0.0, float(timer["ends_at"]) - (self.clock() if now is None else now))

    # ------------------------------------------------------------ stopwatch
    def stopwatch(self, action: str) -> Dict[str, Any]:
        now = self.clock()
        with self.lock:
            sw = self.state["stopwatch"]
            if action == "start":
                if not sw.get("running"):
                    sw["running"] = True
                    sw["started_at"] = now
            elif action == "stop":
                if sw.get("running"):
                    sw["elapsed"] = float(sw.get("elapsed") or 0) + now - float(sw.get("started_at") or now)
                    sw["running"] = False
            elif action == "reset":
                self.state["stopwatch"] = sw = {"running": False, "started_at": 0.0, "elapsed": 0.0, "laps": []}
            elif action == "lap":
                laps = sw.setdefault("laps", [])
                laps.append(round(self.stopwatch_elapsed(now), 2))
                del laps[:-50]
            self.save()
            return {**sw, "value": round(self.stopwatch_elapsed(now), 2)}

    def stopwatch_elapsed(self, now: Optional[float] = None) -> float:
        sw = self.state["stopwatch"]
        now = self.clock() if now is None else now
        total = float(sw.get("elapsed") or 0)
        if sw.get("running"):
            total += now - float(sw.get("started_at") or now)
        return max(0.0, total)

    # ------------------------------------------------------------ pomodoro
    def pomodoro(self, action: str, options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        now = self.clock()
        opts = {**POMODORO_DEFAULTS, **{k: v for k, v in (options or {}).items() if v is not None}}
        with self.lock:
            pomo = self.state["pomodoro"]
            if action == "start":
                pomo = {"active": True, "phase": "work", "cycle": 1, "cycles": int(opts["cycles"]),
                        "work": int(opts["work"]), "short_break": int(opts["short_break"]),
                        "long_break": int(opts["long_break"]), "auto_breaks": bool(opts["auto_breaks"]),
                        "auto_work": bool(opts["auto_work"]), "started": now,
                        "ends_at": now + int(opts["work"]) * 60, "paused_remaining": None, "waiting": False}
                self.state["pomodoro"] = pomo
            elif action == "stop":
                self.state["pomodoro"] = pomo = {"active": False}
            elif action == "pause" and pomo.get("active") and pomo.get("paused_remaining") is None:
                pomo["paused_remaining"] = max(0.0, float(pomo["ends_at"]) - now)
            elif action == "resume" and pomo.get("active"):
                if pomo.get("waiting"):
                    self._pomo_begin(pomo, now)
                elif pomo.get("paused_remaining") is not None:
                    pomo["ends_at"] = now + float(pomo["paused_remaining"])
                    pomo["paused_remaining"] = None
            elif action == "skip" and pomo.get("active"):
                self._pomo_advance(pomo, now, finished=False)
            self.save()
            return dict(self.state["pomodoro"])

    def _pomo_minutes(self, pomo: Dict[str, Any]) -> int:
        return int(pomo.get(pomo.get("phase") or "work") or 25)

    def _pomo_begin(self, pomo: Dict[str, Any], now: float) -> None:
        pomo["waiting"] = False
        pomo["paused_remaining"] = None
        pomo["ends_at"] = now + self._pomo_minutes(pomo) * 60

    def _pomo_advance(self, pomo: Dict[str, Any], now: float, *, finished: bool) -> Dict[str, Any]:
        """Move to the next phase; returns the event describing what just ended."""
        ended = pomo.get("phase")
        if ended == "work":
            if finished:
                day = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
                stats = self.state["stats"].setdefault(day, {"sessions": 0, "minutes": 0})
                stats["sessions"] += 1
                stats["minutes"] += int(pomo.get("work") or 25)
                for key in sorted(self.state["stats"])[:-60]:
                    self.state["stats"].pop(key, None)
            long_break = int(pomo.get("cycle") or 1) % max(1, int(pomo.get("cycles") or 4)) == 0
            pomo["phase"] = "long_break" if long_break else "short_break"
            auto = bool(pomo.get("auto_breaks", True))
        else:
            if ended == "long_break":
                pomo["cycle"] = 1
            else:
                pomo["cycle"] = int(pomo.get("cycle") or 1) + 1
            pomo["phase"] = "work"
            auto = bool(pomo.get("auto_work", False))
        if auto:
            self._pomo_begin(pomo, now)
        else:
            pomo["waiting"] = True
            pomo["ends_at"] = now
            pomo["paused_remaining"] = None
        return {"kind": "pomodoro", "ended": ended, "next": pomo["phase"], "auto": auto, "cycle": pomo.get("cycle")}

    def pomodoro_stats(self, now: Optional[float] = None) -> Dict[str, Any]:
        now = self.clock() if now is None else now
        today = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
        stats = self.state.get("stats") or {}
        week = sorted(stats)[-7:]
        return {"today": stats.get(today, {"sessions": 0, "minutes": 0}),
                "week": {"sessions": sum(stats[d]["sessions"] for d in week),
                         "minutes": sum(stats[d]["minutes"] for d in week)},
                "days": {d: stats[d] for d in week}}

    # ------------------------------------------------------------ ticking
    def tick(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """Fire everything that came due; returns the events (also queued as ``ringing``)."""
        now = self.clock() if now is None else now
        fired: List[Dict[str, Any]] = []
        with self.lock:
            keep = []
            for t in self.state["timers"]:
                if t.get("paused_remaining") is None and float(t["ends_at"]) <= now:
                    late = now - float(t["ends_at"]) > 90
                    event = {"id": t["id"], "kind": t.get("kind", "timer"), "label": t.get("label", ""),
                             "text": t.get("text", ""), "at": now, "late": late, "duration": t.get("duration")}
                    fired.append(event)
                else:
                    keep.append(t)
            if len(keep) != len(self.state["timers"]):
                self.state["timers"] = keep
            pomo = self.state["pomodoro"]
            if pomo.get("active") and not pomo.get("waiting") and pomo.get("paused_remaining") is None \
                    and float(pomo.get("ends_at") or 0) <= now:
                event = self._pomo_advance(pomo, now, finished=True)
                event.update(id="pomodoro-" + _id(), at=now, label="Pomodoro")
                fired.append(event)
            # rings stop by themselves
            self.state["ringing"] = [r for r in self.state["ringing"] if now - float(r.get("at") or now) < RING_SECONDS * 5]
            if fired:
                self.state["ringing"].extend(fired)
                del self.state["ringing"][:-10]
                self.save()
        return fired

    def dismiss(self, ref: str = "") -> int:
        with self.lock:
            before = len(self.state["ringing"])
            self.state["ringing"] = [r for r in self.state["ringing"] if ref and r.get("id") != ref]
            if before != len(self.state["ringing"]):
                self.save()
            return before - len(self.state["ringing"])

    def snooze(self, ref: str, minutes: float = 5) -> Optional[Dict[str, Any]]:
        with self.lock:
            ring = next((r for r in self.state["ringing"] if r.get("id") == ref), None)
            if not ring:
                return None
            self.dismiss(ref)
            return self.add_timer(minutes * 60, ring.get("label") or "", kind=ring.get("kind") if ring.get("kind") in ("timer", "alarm", "reminder") else "timer",
                                  text=ring.get("text") or "")

    def snapshot(self, now: Optional[float] = None) -> Dict[str, Any]:
        now = self.clock() if now is None else now
        with self.lock:
            timers = []
            for t in sorted(self.state["timers"], key=lambda t: float(t["ends_at"])):
                timers.append({**t, "remaining": round(self.remaining(t, now), 1),
                               "paused": t.get("paused_remaining") is not None})
            pomo = dict(self.state["pomodoro"])
            if pomo.get("active"):
                pomo["remaining"] = round(float(pomo["paused_remaining"]) if pomo.get("paused_remaining") is not None
                                          else max(0.0, float(pomo.get("ends_at") or now) - now), 1)
                pomo["total"] = self._pomo_minutes(pomo) * 60
            sw = dict(self.state["stopwatch"])
            sw["value"] = round(self.stopwatch_elapsed(now), 2)
            return {"now": now, "timers": timers, "stopwatch": sw, "pomodoro": pomo,
                    "ringing": list(self.state["ringing"]), "stats": self.pomodoro_stats(now), "version": self.version}

    # ------------------------------------------------------------ thread
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="memuai-timers", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _next_due(self) -> float:
        with self.lock:
            ends = [float(t["ends_at"]) for t in self.state["timers"] if t.get("paused_remaining") is None]
            pomo = self.state["pomodoro"]
            if pomo.get("active") and not pomo.get("waiting") and pomo.get("paused_remaining") is None:
                ends.append(float(pomo.get("ends_at") or 0))
        return min(ends) if ends else math.inf

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                for event in self.tick():
                    if self.on_fire:
                        try:
                            self.on_fire(event)
                        except Exception as exc:
                            log.warning("timer ring failed: %s", exc)
            except Exception as exc:
                log.warning("timer tick failed: %s", exc)
            wait = min(30.0, max(0.05, self._next_due() - self.clock()))
            self._wake.wait(wait)
            self._wake.clear()


# ------------------------------------------------------------------ ring tone

def ring_wav(style: str = "chime", volume: int = 70, *, rate: int = 22050) -> bytes:
    """A short, click-free ring tone as WAV bytes (rendered, no files shipped)."""
    patterns = {
        "chime": [(880.0, 0.00, 0.35), (1108.7, 0.18, 0.35), (1318.5, 0.36, 0.55)],
        "bell": [(659.25, 0.00, 0.9), (987.77, 0.00, 0.6), (1318.5, 0.0, 0.35)],
        "soft": [(523.25, 0.00, 0.45), (659.25, 0.25, 0.45), (783.99, 0.50, 0.7)],
        "digital": [(1760.0, 0.0, 0.08), (1760.0, 0.16, 0.08), (1760.0, 0.32, 0.08), (1760.0, 0.48, 0.08)],
    }
    notes = patterns.get(style, patterns["chime"])
    length = max(start + dur for _f, start, dur in notes) + 0.25
    samples = [0.0] * int(length * rate)
    for freq, start, dur in notes:
        s0, n = int(start * rate), int(dur * rate)
        attack = max(1, int(0.008 * rate))
        for i in range(n):
            env = min(1.0, i / attack) * math.exp(-4.0 * i / n)
            idx = s0 + i
            if idx < len(samples):
                samples[idx] += env * (math.sin(2 * math.pi * freq * i / rate) + 0.25 * math.sin(4 * math.pi * freq * i / rate))
    peak = max(1e-6, max(abs(s) for s in samples))
    gain = 0.85 * max(0, min(100, int(volume))) / 100.0 / peak
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s * gain)) * 32767)) for s in samples))
    return buf.getvalue()


class Ringer:
    """Plays the ring tone in a loop (Windows ``winsound``) until stopped or ``RING_SECONDS`` passed."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def ring(self, style: str = "chime", volume: int = 70, seconds: float = RING_SECONDS) -> bool:
        if sys.platform != "win32" or style == "off" or volume <= 0:
            return False
        self.stop()
        self._stop = stop = threading.Event()
        data = ring_wav(style, volume)

        def play() -> None:
            try:
                import winsound
                deadline = time.monotonic() + seconds
                while not stop.is_set() and time.monotonic() < deadline:
                    winsound.PlaySound(data, winsound.SND_MEMORY | winsound.SND_NODEFAULT)
                    stop.wait(0.6)
            except Exception as exc:
                log.debug("ring failed: %s", exc)

        self._thread = threading.Thread(target=play, name="memuai-ring", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
