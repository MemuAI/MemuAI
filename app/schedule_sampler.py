# -*- coding: utf-8 -*-
"""Round 29: the shared condition sampler of programmable schedules.

One sample answers every rule of the tick: which processes run (psutil — one
``process_iter`` for all names), whether the MemuAI window is open (the heartbeat
of scheduled messages), idle time (``GetLastInputInfo``), the focused window,
AC/battery, internet and the signatures of watched folders. Nothing is measured
unless an enabled rule needs it, and a sample is reused for ``TTL`` seconds, so
the scheduler never polls in a storm whatever the number of rules.

Windows calls go through ctypes and are guarded; on other systems the values a
platform cannot provide are ``None`` (the engine treats them as «cannot check»).
"""
from __future__ import annotations

import fnmatch
import hashlib
import logging
import os
import socket
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .schedule_rules import process_key

log = logging.getLogger("memuai.schedule_sampler")

TTL = 12.0
NETWORK_TTL = 60.0
FOLDER_LIMIT = 4000
_SYSTEM_PROCESSES = {
    "system", "system idle process", "registry", "smss", "csrss", "wininit", "winlogon", "services", "lsass",
    "svchost", "fontdrvhost", "dwm", "memory compression", "spoolsv", "sihost", "ctfmon", "conhost",
    "runtimebroker", "searchindexer", "searchhost", "startmenuexperiencehost", "textinputhost", "dllhost",
    "taskhostw", "wmiprvse", "audiodg", "securityhealthservice", "msmpeng", "nissrv", "wudfhost", "lsaiso",
    "kthreadd", "systemd", "init", "dbus-daemon", "kworker", "rcu_sched", "ksoftirqd", "migration",
}


# --------------------------------------------------------------------------- platform probes

def processes() -> Optional[Set[str]]:
    """Comparison keys (``dota2``) of every running process, or None without psutil."""
    try:
        import psutil
    except Exception:
        return None
    names: Set[str] = set()
    try:
        for proc in psutil.process_iter(["name"]):
            name = proc.info.get("name")
            if name:
                names.add(process_key(name))
    except Exception as exc:  # noqa: BLE001
        log.debug("process_iter failed: %s", exc)
    return names


def _windowed_pids() -> Set[int]:
    """PIDs that own a visible top-level window (Windows) — the picker lists them first."""
    if os.name != "nt":
        return set()
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        pids: Set[int] = set()
        proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def callback(hwnd, _lparam):
            if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                pids.add(int(pid.value))
            return True

        user32.EnumWindows(proto(callback), 0)
        return pids
    except Exception:
        return set()


def process_list(limit: int = 300) -> List[Dict[str, Any]]:
    """Running programs for the picker: one row per executable name, windowed ones first."""
    try:
        import psutil
    except Exception:
        return []
    windowed = _windowed_pids()
    rows: Dict[str, Dict[str, Any]] = {}
    own = os.getpid()
    try:
        for proc in psutil.process_iter(["name", "pid", "memory_info", "exe"]):
            info = proc.info
            name = str(info.get("name") or "").strip()
            if not name or info.get("pid") == own:
                continue
            key = process_key(name)
            if key in _SYSTEM_PROCESSES or key.startswith("kworker"):
                continue
            row = rows.setdefault(key, {"name": name, "count": 0, "memory": 0, "windowed": False, "path": ""})
            row["count"] += 1
            mem = info.get("memory_info")
            row["memory"] += int(getattr(mem, "rss", 0) or 0)
            row["windowed"] = row["windowed"] or info.get("pid") in windowed
            if not row["path"] and info.get("exe"):
                row["path"] = str(info.get("exe"))[:300]
    except Exception as exc:  # noqa: BLE001
        log.debug("process_list failed: %s", exc)
    out = sorted(rows.values(), key=lambda r: (not r["windowed"], -r["memory"], r["name"].lower()))
    return out[:limit]


def idle_seconds() -> Optional[float]:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        tick = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        return max(0.0, ((tick - info.dwTime) & 0xFFFFFFFF) / 1000.0)
    except Exception:
        return None


def focused_window() -> Optional[Dict[str, str]]:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return {"title": "", "process": ""}
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = ""
        try:
            import psutil
            name = psutil.Process(int(pid.value)).name()
        except Exception:
            pass
        return {"title": buffer.value[:300], "process": name[:120]}
    except Exception:
        return None


def power() -> Tuple[Optional[str], Optional[int]]:
    """("ac"|"battery"|None, percent|None). A desktop without a battery is on AC."""
    try:
        import psutil
        battery = psutil.sensors_battery()
    except Exception:
        return None, None
    if battery is None:
        return ("ac", None) if os.name == "nt" else (None, None)
    plugged = battery.power_plugged
    return ("ac" if plugged or plugged is None else "battery"), int(round(battery.percent))


def online(timeout: float = 2.5) -> bool:
    for host, port in (("1.1.1.1", 443), ("8.8.8.8", 443), ("www.microsoft.com", 443)):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def folder_signature(path: str, pattern: str = "") -> str:
    """A cheap fingerprint of a folder (names, sizes, mtimes of its direct entries, capped)."""
    root = Path(os.path.expandvars(os.path.expanduser(str(path or ""))))
    if not root.is_dir():
        return "missing"
    digest = hashlib.sha1()
    count = 0
    try:
        with os.scandir(root) as entries:
            rows = []
            for entry in entries:
                if pattern and not fnmatch.fnmatch(entry.name.lower(), pattern.lower()):
                    continue
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                rows.append((entry.name, st.st_size, st.st_mtime_ns))
                count += 1
                if count >= FOLDER_LIMIT:
                    break
    except OSError:
        return "unreadable"
    for row in sorted(rows):
        digest.update(repr(row).encode("utf-8", "replace"))
    return f"{count}:{digest.hexdigest()[:16]}"


def memuai_open() -> Optional[bool]:
    try:
        from .scheduled_messages import desktop_alive
        return bool(desktop_alive())
    except Exception:
        return None


def boot_time() -> Optional[float]:
    try:
        import psutil
        return float(psutil.boot_time())
    except Exception:
        return None


# --------------------------------------------------------------------------- the sampler

def merge_needs(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"processes": set(), "folders": {}, "flags": set(), "ext": {}}
    for row in rows:
        out["processes"] |= set(row.get("processes") or ())
        out["folders"].update(row.get("folders") or {})
        out["flags"] |= set(row.get("flags") or ())
        out["ext"].update(row.get("ext") or {})   # Round 37: weather / Wi-Fi / holidays (schedule_conds_ext)
    return out


def empty(needs: Dict[str, Any]) -> bool:
    return not (needs.get("processes") or needs.get("folders") or needs.get("flags") or needs.get("ext"))


class Sampler:
    """Holds the previous sample so the engine can see edges (a process just started)."""

    def __init__(self, *, clock=time.time, probes: Optional[Dict[str, Any]] = None) -> None:
        self.clock = clock
        self.started = clock()
        self.probes = {"processes": processes, "idle": idle_seconds, "focus": focused_window, "power": power,
                       "network": online, "folder": folder_signature, "memuai": memuai_open, "boot": boot_time}
        if probes:
            self.probes.update(probes)
        self.last: Optional[Dict[str, Any]] = None
        self.prev: Optional[Dict[str, Any]] = None
        self._network: Tuple[float, Optional[bool]] = (0.0, None)
        self._first_done = False
        self._lock = threading.Lock()

    def _measure(self, needs: Dict[str, Any], now: float) -> Dict[str, Any]:
        flags = set(needs.get("flags") or ())
        sample: Dict[str, Any] = {"ts": now, "flags": sorted(flags), "folder_keys": sorted(needs.get("folders") or {}),
                                  "ext_keys": sorted(needs.get("ext") or {})}
        if needs.get("processes"):
            sample["processes"] = self.probes["processes"]()
        if "memuai" in flags:
            sample["memuai_open"] = self.probes["memuai"]()
        if "idle" in flags:
            sample["idle_seconds"] = self.probes["idle"]()
        if "focus" in flags:
            sample["focus"] = self.probes["focus"]()
        if "power" in flags:
            sample["power"], sample["battery"] = self.probes["power"]()
        if "network" in flags:
            at, value = self._network
            if value is None or now - at >= NETWORK_TTL:
                value = bool(self.probes["network"]())
                self._network = (now, value)
            sample["network"] = value
        folders = needs.get("folders") or {}
        if folders:
            sample["folders"] = {key: self.probes["folder"](spec.get("path"), spec.get("pattern") or "")
                                 for key, spec in list(folders.items())[:20]}
        if needs.get("ext"):   # Round 37: cached inside schedule_conds_ext (weather 20 min, Wi-Fi 30 s)
            try:
                from .schedule_conds_ext import measure
                sample["ext"] = measure(needs["ext"], now, probes=self.probes)
            except Exception as exc:  # noqa: BLE001
                log.debug("ext sample failed: %s", exc)
                sample["ext"] = {}
        return sample

    def _covers(self, sample: Dict[str, Any], needs: Dict[str, Any]) -> bool:
        if needs.get("processes") and "processes" not in sample:
            return False
        if not set(needs.get("flags") or ()) <= set(sample.get("flags") or ()):
            return False
        if not set(needs.get("ext") or {}) <= set(sample.get("ext_keys") or ()):
            return False
        return set(needs.get("folders") or {}) <= set(sample.get("folder_keys") or ())

    def get(self, needs: Dict[str, Any], *, now: Optional[float] = None,
            fresh: bool = False) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        """``(prev, cur)`` for the engine. A reused sample is returned as ``(cur, cur)`` — no edges twice."""
        now = self.clock() if now is None else now
        with self._lock:
            first = not self._first_done
            if empty(needs) and not first:
                return None, {"ts": now}
            if (not first and not fresh and self.last is not None and now - self.last["ts"] < TTL
                    and self._covers(self.last, needs)):
                cur = dict(self.last)
                cur.pop("first", None)
                return cur, cur
            cur = self._measure(needs, now)
            if first:
                cur["first"] = True
                boot = self.probes["boot"]()
                cur["fresh_boot"] = bool(boot and self.started - boot < 15 * 60)
                self._first_done = True
            prev = self.last
            # A sample that did not measure something cannot produce edges for it: keep the older value.
            if prev is not None:
                prev = dict(prev)
            self.prev, self.last = prev, cur
            return prev, cur


SAMPLER = Sampler()
