# -*- coding: utf-8 -*-
"""Панель «Производительность ПК» (Раунд 22): CPU, GPU, память, диски, сеть, температуры, батарея.

Всё локально, без сторонних драйверов и без LibreHardwareMonitor:

* если установлен ``psutil`` — он (CPU, память, диски, сеть, батарея, температуры на Linux);
* на Windows без psutil — WinAPI через ctypes: ``GetSystemTimes`` (CPU), ``GlobalMemoryStatusEx``,
  ``GetDiskFreeSpaceExW``, ``GetSystemPowerStatus``, ``GetTickCount64`` и счётчики PDH
  (``\\Network Interface(*)\\Bytes …/sec``, ``\\PhysicalDisk(_Total)\\Disk … Bytes/sec``,
  ``\\GPU Engine(*engtype_3D)\\Utilization Percentage``, ``\\GPU Adapter Memory(*)\\Dedicated Usage``,
  ``\\Thermal Zone Information(*)\\Temperature`` — термозона ACPI, без прав администратора);
* NVIDIA — ``nvidia-smi`` (загрузка, память, температура GPU), если он есть в PATH;
* на Linux — ``/proc`` и ``/sys`` (так модуль проверяется в песочнице).

Чего нет — то ``None``, карточка честно пишет «нет данных», числа не придумываются.
Скорости (сеть, диск) считаются по двум замерам; первый вызов делает короткий двойной замер.
"""
from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("memuai.panels.system")
IS_WINDOWS = sys.platform == "win32"
_LOCK = threading.Lock()
_STATE: Dict[str, Any] = {"prev": None, "cache": None, "cache_at": 0.0, "pdh": None, "nvsmi": None, "nvsmi_at": 0.0,
                          "static": None}
CACHE_SECONDS = 1.2

def _pct(used: Optional[float], total: Optional[float]) -> Optional[float]:
    if used is None or not total:
        return None
    return round(max(0.0, min(100.0, used * 100.0 / total)), 1)

def fmt_bytes(value: Optional[float], lang: str = "ru", *, rate: bool = False) -> str:
    if value is None:
        return "—"
    units = (["Б", "КБ", "МБ", "ГБ", "ТБ"] if lang != "en" else ["B", "KB", "MB", "GB", "TB"])
    number = float(value)
    index = 0
    while number >= 1024 and index < len(units) - 1:
        number /= 1024
        index += 1
    digits = 0 if number >= 100 or index == 0 else 1
    text = f"{number:.{digits}f}"
    if text.endswith(".0"):
        text = text[:-2]
    if lang != "en":
        text = text.replace(".", ",")
    return f"{text} {units[index]}" + (("/с" if lang != "en" else "/s") if rate else "")

def fmt_bits(value: Optional[float], lang: str = "ru") -> str:
    """Скорость в битах (как у провайдера): 12,4 Мбит/с."""
    if value is None:
        return "—"
    bits = float(value) * 8
    units = (["бит/с", "Кбит/с", "Мбит/с", "Гбит/с"] if lang != "en" else ["bit/s", "kbit/s", "Mbit/s", "Gbit/s"])
    index = 0
    while bits >= 1000 and index < len(units) - 1:
        bits /= 1000
        index += 1
    text = f"{bits:.{0 if bits >= 100 or index == 0 else 1}f}"
    if text.endswith(".0"):
        text = text[:-2]
    if lang != "en":
        text = text.replace(".", ",")
    return f"{text} {units[index]}"

def fmt_duration(seconds: Optional[float], lang: str = "ru") -> str:
    if seconds is None or seconds < 0:
        return "—"
    minutes = int(seconds // 60)
    days, hours, mins = minutes // 1440, (minutes % 1440) // 60, minutes % 60
    if lang == "en":
        return f"{days}d {hours}h" if days else (f"{hours}h {mins}m" if hours else f"{mins}m")
    return f"{days} д {hours} ч" if days else (f"{hours} ч {mins} мин" if hours else f"{mins} мин")

def _to_f(value: Optional[float]) -> Optional[float]:
    return None if value is None else value * 9 / 5 + 32

def _run(cmd: List[str], timeout: float = 2.5) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:  # noqa: BLE001
        return ""

def _win_cpu_times() -> Optional[Tuple[float, float]]:
    import ctypes
    from ctypes import wintypes

    class FILETIME(ctypes.Structure):
        _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]

    idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
    fn = ctypes.windll.kernel32.GetSystemTimes
    fn.argtypes = [ctypes.POINTER(FILETIME)] * 3
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        return None
    val = lambda f: (f.hi << 32 | f.lo) / 1e7  # noqa: E731
    return val(idle), val(kernel) + val(user)

def _win_memory() -> Optional[Dict[str, float]]:
    import ctypes
    from ctypes import wintypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD), ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    fn = ctypes.windll.kernel32.GlobalMemoryStatusEx
    fn.argtypes = [ctypes.POINTER(MEMORYSTATUSEX)]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(stat)):
        return None
    return {"total": float(stat.ullTotalPhys), "used": float(stat.ullTotalPhys - stat.ullAvailPhys)}

def _win_disks() -> List[Dict[str, Any]]:
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    k32.GetLogicalDriveStringsW.argtypes = [wintypes.DWORD, wintypes.LPWSTR]
    k32.GetLogicalDriveStringsW.restype = wintypes.DWORD
    k32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    k32.GetDriveTypeW.restype = wintypes.UINT
    k32.GetDiskFreeSpaceExW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_ulonglong),
                                        ctypes.POINTER(ctypes.c_ulonglong), ctypes.POINTER(ctypes.c_ulonglong)]
    k32.GetDiskFreeSpaceExW.restype = wintypes.BOOL
    buf = ctypes.create_unicode_buffer(512)
    length = k32.GetLogicalDriveStringsW(511, buf)
    out = []
    for root in buf[:length].split("\x00"):
        if not root or k32.GetDriveTypeW(root) != 3:
            continue
        free, total, _free_all = ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong()
        if k32.GetDiskFreeSpaceExW(root, ctypes.byref(free), ctypes.byref(total), ctypes.byref(_free_all)) and total.value:
            out.append({"mount": root.rstrip("\\"), "total": float(total.value), "used": float(total.value - _free_all.value)})
    return out[:8]

def _win_battery() -> Optional[Dict[str, Any]]:
    import ctypes
    from ctypes import wintypes

    class SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte), ("BatteryLifePercent", ctypes.c_ubyte),
                    ("SystemStatusFlag", ctypes.c_ubyte), ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]

    status = SYSTEM_POWER_STATUS()
    fn = ctypes.windll.kernel32.GetSystemPowerStatus
    fn.argtypes = [ctypes.POINTER(SYSTEM_POWER_STATUS)]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(status)) or status.BatteryFlag & 128 or status.BatteryLifePercent == 255:
        return None
    secs = None if status.BatteryLifeTime == 0xFFFFFFFF else int(status.BatteryLifeTime)
    return {"pct": float(status.BatteryLifePercent), "plugged": status.ACLineStatus == 1, "secs_left": secs}

def _win_uptime() -> Optional[float]:
    import ctypes
    fn = ctypes.windll.kernel32.GetTickCount64
    fn.restype = ctypes.c_ulonglong
    return fn() / 1000.0

class _Pdh:
    """Счётчики PDH с подстановками (*): один запрос живёт между замерами (скорости считает PDH)."""

    COUNTERS = {
        "net_rx": r"\Network Interface(*)\Bytes Received/sec",
        "net_tx": r"\Network Interface(*)\Bytes Sent/sec",
        "disk_r": r"\PhysicalDisk(_Total)\Disk Read Bytes/sec",
        "disk_w": r"\PhysicalDisk(_Total)\Disk Write Bytes/sec",
        "gpu": r"\GPU Engine(*engtype_3D)\Utilization Percentage",
        "gpu_mem": r"\GPU Adapter Memory(*)\Dedicated Usage",
        "thermal": r"\Thermal Zone Information(*)\Temperature",

        "cores": r"\Processor(*)\% Processor Time",
        "swap": r"\Paging File(_Total)\% Usage",
    }

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes
        self.ctypes = ctypes
        self.pdh = ctypes.WinDLL("pdh")
        self.query = ctypes.c_void_p()
        self.pdh.PdhOpenQueryW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        self.pdh.PdhOpenQueryW.restype = wintypes.LONG
        self.pdh.PdhAddEnglishCounterW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_void_p,
                                                   ctypes.POINTER(ctypes.c_void_p)]
        self.pdh.PdhAddEnglishCounterW.restype = wintypes.LONG
        self.pdh.PdhCollectQueryData.argtypes = [ctypes.c_void_p]
        self.pdh.PdhCollectQueryData.restype = wintypes.LONG
        self.pdh.PdhGetFormattedCounterArrayW.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                                          ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        self.pdh.PdhGetFormattedCounterArrayW.restype = wintypes.LONG
        if self.pdh.PdhOpenQueryW(None, None, ctypes.byref(self.query)) != 0:
            raise OSError("PdhOpenQueryW failed")
        self.handles: Dict[str, Any] = {}
        for key, path in self.COUNTERS.items():
            handle = ctypes.c_void_p()
            if self.pdh.PdhAddEnglishCounterW(self.query, path, None, ctypes.byref(handle)) == 0:
                self.handles[key] = handle
        self.pdh.PdhCollectQueryData(self.query)
        self.primed = time.monotonic()

    def read(self) -> Dict[str, List[Tuple[str, float]]]:
        ctypes = self.ctypes
        from ctypes import wintypes

        class VALUE(ctypes.Union):
            _fields_ = [("longValue", ctypes.c_long), ("doubleValue", ctypes.c_double), ("largeValue", ctypes.c_longlong),
                        ("AnsiStringValue", ctypes.c_char_p), ("WideStringValue", ctypes.c_wchar_p)]

        class FMT_VALUE(ctypes.Structure):
            _fields_ = [("CStatus", wintypes.DWORD), ("u", VALUE)]

        class ITEM(ctypes.Structure):
            _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", FMT_VALUE)]

        if time.monotonic() - self.primed < 0.2:
            time.sleep(0.25)
        self.pdh.PdhCollectQueryData(self.query)
        out: Dict[str, List[Tuple[str, float]]] = {}
        PDH_FMT_DOUBLE, PDH_MORE_DATA = 0x00000200, 0x800007D2
        for key, handle in self.handles.items():
            size, count = wintypes.DWORD(0), wintypes.DWORD(0)
            status = self.pdh.PdhGetFormattedCounterArrayW(handle, PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), None)
            if (status & 0xFFFFFFFF) != PDH_MORE_DATA or not size.value:
                continue
            buf = (ctypes.c_byte * size.value)()
            if self.pdh.PdhGetFormattedCounterArrayW(handle, PDH_FMT_DOUBLE, ctypes.byref(size), ctypes.byref(count), buf) != 0:
                continue
            items = ctypes.cast(buf, ctypes.POINTER(ITEM))
            rows = []
            for i in range(count.value):
                item = items[i]
                if item.FmtValue.CStatus in (0, 1):
                    rows.append((item.szName or "", float(item.FmtValue.u.doubleValue)))
            out[key] = rows
        return out

def _win_registry(path: str, name: str) -> str:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            return str(winreg.QueryValueEx(key, name)[0]).strip()
    except Exception:  # noqa: BLE001
        return ""

def _win_gpu_name() -> str:
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    names = []
    for index in range(4):
        value = _win_registry(base + f"\\{index:04d}", "DriverDesc")
        if value and "basic" not in value.lower() and "remote" not in value.lower():
            names.append(value)
    return names[0] if names else ""

def _nvidia() -> Optional[List[Dict[str, Any]]]:
    now = time.monotonic()
    if _STATE["nvsmi_at"] and now - _STATE["nvsmi_at"] < 4:
        return _STATE["nvsmi"]
    _STATE["nvsmi_at"] = now
    exe = shutil.which("nvidia-smi")
    if not exe:
        _STATE["nvsmi"] = None
        _STATE["nvsmi_at"] = now + 3600
        return None
    raw = _run([exe, "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                "--format=csv,noheader,nounits"])
    gpus = []
    for line in raw.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue

        def num(text):
            try:
                return float(text)
            except ValueError:
                return None

        gpus.append({"name": parts[0], "util": num(parts[1]), "mem_used": (num(parts[2]) or 0) * 1048576 if num(parts[2]) is not None else None,
                     "mem_total": (num(parts[3]) or 0) * 1048576 if num(parts[3]) is not None else None, "temp": num(parts[4])})
    _STATE["nvsmi"] = gpus or None
    return _STATE["nvsmi"]

def _linux_cpu_times() -> Optional[Tuple[float, float]]:
    try:
        with open("/proc/stat", encoding="ascii") as handle:
            parts = handle.readline().split()[1:]
        values = [float(v) for v in parts]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return idle, sum(values[:8])
    except (OSError, ValueError, IndexError):
        return None

def _linux_cpu_per() -> Optional[List[Tuple[float, float]]]:
    try:
        out = []
        with open("/proc/stat", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("cpu") and line[3:4].isdigit():
                    values = [float(v) for v in line.split()[1:]]
                    out.append((values[3] + (values[4] if len(values) > 4 else 0), sum(values[:8])))
        return out or None
    except (OSError, ValueError, IndexError):
        return None

def _linux_swap() -> Optional[Dict[str, float]]:
    try:
        info = {}
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                if key in ("SwapTotal", "SwapFree"):
                    info[key] = float(rest.split()[0]) * 1024
        return {"total": info["SwapTotal"], "used": info["SwapTotal"] - info.get("SwapFree", 0)}
    except (OSError, ValueError, KeyError):
        return None

def _linux_nets() -> Optional[Dict[str, Tuple[float, float]]]:
    try:
        out: Dict[str, Tuple[float, float]] = {}
        with open("/proc/net/dev", encoding="ascii") as handle:
            for line in handle.readlines()[2:]:
                name, _, rest = line.partition(":")
                parts = rest.split()
                if name.strip() and name.strip() != "lo":
                    out[name.strip()] = (float(parts[0]), float(parts[8]))
        return out
    except (OSError, ValueError, IndexError):
        return None

def _linux_hwmon() -> Tuple[List[Tuple[str, float]], List[Tuple[str, float]]]:
    """Все датчики температуры и вентиляторов из /sys/class/hwmon (подписи — как у драйвера)."""
    temps: List[Tuple[str, float]] = []
    fans: List[Tuple[str, float]] = []
    base = "/sys/class/hwmon"
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return temps, fans
    for hw in names[:16]:
        path = os.path.join(base, hw)
        try:
            with open(os.path.join(path, "name"), encoding="utf-8") as handle:
                chip = handle.read().strip()
        except OSError:
            chip = hw
        try:
            files = os.listdir(path)
        except OSError:
            continue
        for fname in sorted(files):
            match = re.fullmatch(r"(temp|fan)(\d+)_input", fname)
            if not match:
                continue
            try:
                with open(os.path.join(path, fname), encoding="ascii") as handle:
                    value = float(handle.read().strip())
            except (OSError, ValueError):
                continue
            label = ""
            try:
                with open(os.path.join(path, f"{match.group(1)}{match.group(2)}_label"), encoding="utf-8") as handle:
                    label = handle.read().strip()
            except OSError:
                pass
            label = f"{chip} {label}".strip() if label else f"{chip} {match.group(1)}{match.group(2)}"
            if match.group(1) == "temp" and 5 < value / 1000 < 130:
                temps.append((label[:40], round(value / 1000, 1)))
            elif match.group(1) == "fan" and 0 <= value < 30000:
                fans.append((label[:40], value))
    return temps[:12], fans[:8]

def _linux_memory() -> Optional[Dict[str, float]]:
    try:
        info = {}
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                info[key] = float(rest.split()[0]) * 1024
        total = info["MemTotal"]
        avail = info.get("MemAvailable", info.get("MemFree", 0))
        return {"total": total, "used": total - avail}
    except (OSError, ValueError, KeyError):
        return None

def _linux_net() -> Optional[Tuple[float, float]]:
    try:
        rx = tx = 0.0
        with open("/proc/net/dev", encoding="ascii") as handle:
            for line in handle.readlines()[2:]:
                name, _, rest = line.partition(":")
                if name.strip() == "lo":
                    continue
                parts = rest.split()
                rx += float(parts[0])
                tx += float(parts[8])
        return rx, tx
    except (OSError, ValueError, IndexError):
        return None

def _linux_disk_io() -> Optional[Tuple[float, float]]:
    try:
        read = write = 0.0
        with open("/proc/diskstats", encoding="ascii") as handle:
            for line in handle:
                parts = line.split()
                name = parts[2]
                if name.startswith(("loop", "ram", "dm-")) or name[-1].isdigit() and not name.startswith("nvme"):
                    continue
                if name.startswith("nvme") and "p" in name[4:]:
                    continue
                read += float(parts[5]) * 512
                write += float(parts[9]) * 512
        return read, write
    except (OSError, ValueError, IndexError):
        return None

def _linux_temp() -> Optional[float]:
    best = None
    base = "/sys/class/thermal"
    try:
        for name in os.listdir(base):
            if not name.startswith("thermal_zone"):
                continue
            try:
                with open(os.path.join(base, name, "temp"), encoding="ascii") as handle:
                    value = float(handle.read().strip()) / 1000
                if 5 < value < 130:
                    best = max(best or 0, value)
            except (OSError, ValueError):
                continue
    except OSError:
        return None
    return best

def _linux_battery() -> Optional[Dict[str, Any]]:
    base = "/sys/class/power_supply"
    try:
        for name in os.listdir(base):
            path = os.path.join(base, name)
            try:
                with open(os.path.join(path, "type"), encoding="ascii") as handle:
                    if handle.read().strip() != "Battery":
                        continue
                with open(os.path.join(path, "capacity"), encoding="ascii") as handle:
                    pct = float(handle.read().strip())
                status = ""
                try:
                    with open(os.path.join(path, "status"), encoding="ascii") as handle:
                        status = handle.read().strip()
                except OSError:
                    pass
                return {"pct": pct, "plugged": status in ("Charging", "Full", "Not charging"), "secs_left": None}
            except (OSError, ValueError):
                continue
    except OSError:
        return None
    return None

def _linux_cpu_name() -> str:
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or ""

def _linux_uptime() -> Optional[float]:
    try:
        with open("/proc/uptime", encoding="ascii") as handle:
            return float(handle.read().split()[0])
    except (OSError, ValueError):
        return None

def _posix_disks() -> List[Dict[str, Any]]:
    mounts = ["/"]
    home = os.path.expanduser("~")
    if home not in mounts:
        mounts.append(home)
    out, seen = [], set()
    for mount in mounts:
        try:
            usage = shutil.disk_usage(mount)
            key = (usage.total, usage.used)
            if key in seen:
                continue
            seen.add(key)
            out.append({"mount": mount, "total": float(usage.total), "used": float(usage.used)})
        except OSError:
            continue
    return out

def _psutil():
    try:
        import psutil  # type: ignore
        return psutil
    except ImportError:
        return None

def _static_info() -> Dict[str, Any]:
    if _STATE["static"] is None:
        cpu_name = ""
        if IS_WINDOWS:
            cpu_name = _win_registry(r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString")
        elif sys.platform.startswith("linux"):
            cpu_name = _linux_cpu_name()
        cpu_name = " ".join(cpu_name.replace("(R)", "").replace("(TM)", "").replace("CPU", "").split())
        _STATE["static"] = {"cpu_name": cpu_name or platform.processor() or "", "cores": os.cpu_count() or 0,
                            "gpu_name": _win_gpu_name() if IS_WINDOWS else "", "host": platform.node()[:40],
                            "os": f"{platform.system()} {platform.release()}".strip()}
    return _STATE["static"]

def _raw() -> Dict[str, Any]:
    """Один замер счётчиков (накопительные значения; скорости — в :func:`sample`)."""
    ps = _psutil()
    raw: Dict[str, Any] = {"t": time.monotonic()}
    try:
        if ps is not None:
            times = ps.cpu_times()
            idle = getattr(times, "idle", 0.0) + getattr(times, "iowait", 0.0)
            raw["cpu"] = (idle, sum(times))
            mem = ps.virtual_memory()
            raw["mem"] = {"total": float(mem.total), "used": float(mem.total - mem.available)}
            io = ps.net_io_counters()
            raw["net"] = (float(io.bytes_recv), float(io.bytes_sent)) if io else None
            try:
                dio = ps.disk_io_counters()
                raw["disk_io"] = (float(dio.read_bytes), float(dio.write_bytes)) if dio else None
            except Exception:  # noqa: BLE001
                raw["disk_io"] = None
            disks = []
            for part in ps.disk_partitions(all=False)[:8]:
                if IS_WINDOWS and "fixed" not in (part.opts or "") and "rw" not in (part.opts or ""):
                    continue
                try:
                    usage = ps.disk_usage(part.mountpoint)
                    if usage.total < 1 << 30 or (not IS_WINDOWS and (part.fstype or "") in ("squashfs", "tmpfs", "overlay", "fuse", "devtmpfs")):
                        continue
                    if any(abs(d["total"] - usage.total) < 1 and abs(d["used"] - usage.used) < (1 << 20) for d in disks):
                        continue
                    disks.append({"mount": part.mountpoint.rstrip("\\") or part.mountpoint, "total": float(usage.total), "used": float(usage.used)})
                except Exception:  # noqa: BLE001
                    continue
            raw["disks"] = disks or _posix_disks()
            battery = ps.sensors_battery() if hasattr(ps, "sensors_battery") else None
            raw["battery"] = ({"pct": float(battery.percent), "plugged": bool(battery.power_plugged),
                               "secs_left": battery.secsleft if isinstance(battery.secsleft, int) and battery.secsleft > 0 else None}
                              if battery else None)
            temp = None
            if hasattr(ps, "sensors_temperatures"):
                try:
                    for name, rows in (ps.sensors_temperatures() or {}).items():
                        for row in rows:
                            if row.current and 5 < row.current < 130 and (name.lower() in ("coretemp", "k10temp", "zenpower", "cpu_thermal")
                                                                          or temp is None):
                                temp = max(temp or 0, float(row.current))
                except Exception:  # noqa: BLE001
                    temp = None
            raw["temp"] = temp
            raw["uptime"] = time.time() - ps.boot_time()

            try:
                raw["cpu_per"] = [(getattr(c, "idle", 0.0) + getattr(c, "iowait", 0.0), sum(c)) for c in ps.cpu_times(percpu=True)]
            except Exception:  # noqa: BLE001
                raw["cpu_per"] = None
            try:
                sw = ps.swap_memory()
                raw["swap"] = {"total": float(sw.total), "used": float(sw.used)}
            except Exception:  # noqa: BLE001
                raw["swap"] = None
            try:
                raw["nets"] = {name: (float(c.bytes_recv), float(c.bytes_sent))
                               for name, c in (ps.net_io_counters(pernic=True) or {}).items()}
            except Exception:  # noqa: BLE001
                raw["nets"] = None
            temps_all: List[Tuple[str, float]] = []
            fans_all: List[Tuple[str, float]] = []
            if hasattr(ps, "sensors_temperatures"):
                try:
                    for name, rows in (ps.sensors_temperatures() or {}).items():
                        for index, row in enumerate(rows):
                            if row.current and 5 < row.current < 130:
                                temps_all.append(((f"{name} {row.label}".strip() if row.label else f"{name} {index + 1}")[:40],
                                                  round(float(row.current), 1)))
                except Exception:  # noqa: BLE001
                    pass
            if hasattr(ps, "sensors_fans"):
                try:
                    for name, rows in (ps.sensors_fans() or {}).items():
                        for index, row in enumerate(rows):
                            fans_all.append(((f"{name} {row.label}".strip() if row.label else f"{name} {index + 1}")[:40],
                                             float(row.current)))
                except Exception:  # noqa: BLE001
                    pass
            raw["temps"], raw["fans"] = temps_all[:12], fans_all[:8]
        elif IS_WINDOWS:
            raw["cpu"] = _win_cpu_times()
            raw["mem"] = _win_memory()
            raw["disks"] = _win_disks()
            raw["battery"] = _win_battery()
            raw["uptime"] = _win_uptime()
        else:
            raw["cpu"] = _linux_cpu_times()
            raw["mem"] = _linux_memory()
            raw["net"] = _linux_net()
            raw["disk_io"] = _linux_disk_io()
            raw["disks"] = _posix_disks()
            raw["battery"] = _linux_battery()
            raw["temp"] = _linux_temp()
            raw["uptime"] = _linux_uptime()
            raw["cpu_per"] = _linux_cpu_per()
            raw["swap"] = _linux_swap()
            raw["nets"] = _linux_nets()
            raw["temps"], raw["fans"] = _linux_hwmon()
    except Exception as exc:  # noqa: BLE001 — замер никогда не роняет окно
        log.info("system: sample failed: %s", exc)
    if IS_WINDOWS:
        try:
            if _STATE["pdh"] is None:
                _STATE["pdh"] = _Pdh()
            raw["pdh"] = _STATE["pdh"].read()
        except Exception as exc:  # noqa: BLE001
            _STATE["pdh"] = False
            log.info("system: PDH unavailable: %s", exc)
    return raw

def _rate(prev: Optional[Tuple[float, float]], cur: Optional[Tuple[float, float]], dt: float) -> Tuple[Optional[float], Optional[float]]:
    if not prev or not cur or dt <= 0:
        return None, None
    return max(0.0, (cur[0] - prev[0]) / dt), max(0.0, (cur[1] - prev[1]) / dt)

def sample(*, lang: str = "ru") -> Dict[str, Any]:
    """Свежий замер (кэш ~1 с, чтобы несколько карточек не мерили по очереди)."""
    with _LOCK:
        now = time.monotonic()
        if _STATE["cache"] is not None and now - _STATE["cache_at"] < CACHE_SECONDS and _STATE["cache"].get("lang") == lang:
            return dict(_STATE["cache"])
        prev = _STATE["prev"]
        if prev is None or now - prev["t"] > 30:
            prev = _raw()
            time.sleep(0.3)
        cur = _raw()
        _STATE["prev"] = cur
        dt = cur["t"] - prev["t"]
        out = build(prev, cur, dt, lang=lang)
        _STATE["cache"], _STATE["cache_at"] = out, time.monotonic()
        return dict(out)

def build(prev: Dict[str, Any], cur: Dict[str, Any], dt: float, *, lang: str = "ru") -> Dict[str, Any]:
    """Значения для карточки из двух сырых замеров (чистая функция — проверяется тестами)."""
    static = _static_info()
    cpu = None
    if prev.get("cpu") and cur.get("cpu"):
        didle = cur["cpu"][0] - prev["cpu"][0]
        dtotal = cur["cpu"][1] - prev["cpu"][1]
        if dtotal > 0:
            cpu = round(max(0.0, min(100.0, (1 - didle / dtotal) * 100)), 1)
    mem = cur.get("mem") or {}
    ram = _pct(mem.get("used"), mem.get("total"))
    disks = cur.get("disks") or []
    main_disk = disks[0] if disks else None
    disk_pct = _pct(main_disk["used"], main_disk["total"]) if main_disk else None
    net_down, net_up = _rate(prev.get("net"), cur.get("net"), dt)
    disk_r, disk_w = _rate(prev.get("disk_io"), cur.get("disk_io"), dt)
    pdh = cur.get("pdh") or {}
    if pdh:
        skip = ("isatap", "teredo", "loopback", "pseudo")
        if net_down is None and pdh.get("net_rx") is not None:
            net_down = sum(v for name, v in pdh["net_rx"] if not any(s in name.lower() for s in skip))
        if net_up is None and pdh.get("net_tx") is not None:
            net_up = sum(v for name, v in pdh["net_tx"] if not any(s in name.lower() for s in skip))
        if disk_r is None and pdh.get("disk_r"):
            disk_r = pdh["disk_r"][0][1]
        if disk_w is None and pdh.get("disk_w"):
            disk_w = pdh["disk_w"][0][1]
    gpu = gpu_mem_used = gpu_mem_total = gpu_temp = None
    gpu_name = static.get("gpu_name") or ""
    nv = _nvidia() if (IS_WINDOWS or sys.platform.startswith("linux")) else None
    if nv:
        gpu, gpu_temp, gpu_name = nv[0]["util"], nv[0]["temp"], nv[0]["name"] or gpu_name
        gpu_mem_used, gpu_mem_total = nv[0]["mem_used"], nv[0]["mem_total"]
    elif pdh.get("gpu") is not None:
        per_adapter: Dict[str, float] = {}
        for name, value in pdh["gpu"]:
            key = name.split("_phys_")[0].split("luid_")[-1][:40] if "luid_" in name else "gpu"
            per_adapter[key] = per_adapter.get(key, 0.0) + value
        gpu = round(min(100.0, max(per_adapter.values())), 1) if per_adapter else 0.0
        if pdh.get("gpu_mem"):
            gpu_mem_used = max(v for _name, v in pdh["gpu_mem"])
    temp = cur.get("temp")
    temp_src = "cpu" if temp is not None else ""
    if temp is None and pdh.get("thermal"):
        kelvin = [v for _name, v in pdh["thermal"] if 250 < v < 420]
        if kelvin:
            temp = round(max(kelvin) - 273.15, 1)
            temp_src = "acpi"
    battery = cur.get("battery")
    t = (lambda ru, en: en if lang == "en" else ru)
    dash = "—"

    cores: List[float] = []
    if prev.get("cpu_per") and cur.get("cpu_per") and len(prev["cpu_per"]) == len(cur["cpu_per"]):
        for (i0, t0), (i1, t1) in zip(prev["cpu_per"], cur["cpu_per"]):
            span = t1 - t0
            cores.append(round(max(0.0, min(100.0, (1 - (i1 - i0) / span) * 100)), 1) if span > 0 else 0.0)
    elif pdh.get("cores"):
        rows = [(name, v) for name, v in pdh["cores"] if name and name != "_Total"]
        rows.sort(key=lambda r: [int(x) if x.isdigit() else 0 for x in re.split(r"[^0-9]+", r[0]) if x != ""] or [0])
        cores = [round(max(0.0, min(100.0, v)), 1) for _name, v in rows]
    swap = cur.get("swap") or None
    swap_pct = _pct(swap["used"], swap["total"]) if swap and swap.get("total") else None
    swap_caption = (f"{fmt_bytes(swap['used'], lang)} / {fmt_bytes(swap['total'], lang)}" if swap and swap.get("total")
                    else (t("нет подкачки", "no swap") if swap else dash))
    if swap_pct is None and pdh.get("swap"):
        swap_pct = round(max(0.0, min(100.0, pdh["swap"][0][1])), 1)
        swap_caption = ""
    nets: List[Dict[str, Any]] = []
    nets_bits: List[Dict[str, Any]] = []
    per_adapter: List[Tuple[str, Optional[float], Optional[float]]] = []
    if prev.get("nets") and cur.get("nets") and dt > 0:
        for name, (rx, tx) in cur["nets"].items():
            old = prev["nets"].get(name)
            if old is None or name.lower() in ("lo", "loopback") or name.lower().startswith(("loopback", "lo0")):
                continue
            per_adapter.append((name, max(0.0, (rx - old[0]) / dt), max(0.0, (tx - old[1]) / dt)))
    elif pdh.get("net_rx") is not None:
        tx_by = dict(pdh.get("net_tx") or [])
        for name, rx in pdh["net_rx"]:
            if any(x in name.lower() for x in ("isatap", "teredo", "loopback", "pseudo")):
                continue
            per_adapter.append((name, rx, tx_by.get(name)))
    for name, down, up in per_adapter[:12]:
        nets.append({"title": name[:40], "value": f"↓ {fmt_bytes(down, lang, rate=True)} · ↑ {fmt_bytes(up, lang, rate=True)}"})
        nets_bits.append({"title": name[:40], "value": f"↓ {fmt_bits(down, lang)} · ↑ {fmt_bits(up, lang)}"})
    temps_all = list(cur.get("temps") or [])
    if not temps_all and pdh.get("thermal"):
        for name, kelvin in pdh["thermal"]:
            if 250 < kelvin < 420:
                temps_all.append(((t("ACPI ", "ACPI ") + (name.split("\\")[-1] or "zone"))[:40], round(kelvin - 273.15, 1)))
    nv_temp = None
    fans_all = list(cur.get("fans") or [])
    if IS_WINDOWS:
        temps_note = t("Windows не отдаёт температуры процессора без драйверов производителя; показаны только доступные датчики.",
                       "Windows does not expose CPU temperatures without vendor drivers; only the available sensors are shown.")
        fans_note = t("Обороты вентиляторов Windows без драйверов производителя не отдаёт.",
                      "Windows does not expose fan speeds without vendor drivers.")
    else:
        temps_note = t("Датчики температуры не найдены.", "No temperature sensors found.")
        fans_note = t("Датчики вентиляторов не найдены.", "No fan sensors found.")

    def pct_text(value):
        return dash if value is None else (f"{value:.0f}")

    stats_disks = [{"mount": d["mount"], "pct": _pct(d["used"], d["total"]), "used": fmt_bytes(d["used"], lang),
                    "total": fmt_bytes(d["total"], lang)} for d in disks[:4]]
    disks_list = [{"label": d["mount"], "pct": _pct(d["used"], d["total"]),
                   "caption": f"{fmt_bytes(d['total'] - d['used'], lang)} {t('своб.', 'free')} · {fmt_bytes(d['total'], lang)}"}
                  for d in disks[:8]]
    disks_rows = [{"title": d["label"], "value": f"{d['pct']:.0f} %" if d["pct"] is not None else dash, "subtitle": d["caption"]}
                  for d in disks_list]
    if gpu_temp is not None:
        temps_all.append(("GPU", float(gpu_temp)))
        nv_temp = gpu_temp

    def temp_rows(fahrenheit: bool) -> List[Dict[str, Any]]:
        return [{"title": label, "value": f"{(_to_f(v) if fahrenheit else v):.0f}°"} for label, v in temps_all[:12]]

    def temp_meters(fahrenheit: bool) -> List[Dict[str, Any]]:
        return [{"label": label, "pct": max(0.0, min(100.0, v)), "caption": f"{(_to_f(v) if fahrenheit else v):.0f}°"}
                for label, v in temps_all[:12]]

    vram_pct = _pct(gpu_mem_used, gpu_mem_total) if gpu_mem_used is not None and gpu_mem_total else None
    battery_caption = ""
    if battery:
        if battery.get("plugged"):
            battery_caption = t("от сети", "plugged in")
        elif battery.get("secs_left"):
            battery_caption = t("осталось ", "left ") + fmt_duration(float(battery["secs_left"]), lang)
        else:
            battery_caption = t("от батареи", "on battery")
    battery_text = ""
    if battery:
        battery_text = f"{battery['pct']:.0f}%" + (" ⚡" if battery.get("plugged") else "")
    return {
        "lang": lang, "at": time.time(),
        "cpu": cpu, "cpu_text": pct_text(cpu), "cpu_name": static.get("cpu_name") or t("Процессор", "CPU"),
        "threads": static.get("cores") or None,
        "ram": ram, "ram_text": pct_text(ram), "ram_used": fmt_bytes(mem.get("used"), lang), "ram_total": fmt_bytes(mem.get("total"), lang),
        "ram_caption": f"{fmt_bytes(mem.get('used'), lang)} / {fmt_bytes(mem.get('total'), lang)}" if mem else dash,
        "gpu": gpu, "gpu_text": pct_text(gpu), "gpu_name": gpu_name or t("Видеокарта", "GPU"),
        "gpu_caption": (f"{fmt_bytes(gpu_mem_used, lang)}" + (f" / {fmt_bytes(gpu_mem_total, lang)}" if gpu_mem_total else ""))
        if gpu_mem_used is not None else (t("нет данных", "no data") if gpu is None else ""),
        "gpu_temp": gpu_temp, "gpu_temp_text": dash if gpu_temp is None else f"{gpu_temp:.0f}°",
        "disk": disk_pct, "disk_text": pct_text(disk_pct),
        "disk_label": (t("Диск ", "Disk ") + main_disk["mount"]).strip() if main_disk and main_disk["mount"] not in ("/",) else t("Диск", "Disk"),
        "disk_caption": f"{fmt_bytes(main_disk['total'] - main_disk['used'], lang)} {t('своб.', 'free')}" if main_disk else dash,
        "disks": stats_disks,
        "net_down": fmt_bytes(net_down, lang, rate=True), "net_up": fmt_bytes(net_up, lang, rate=True),
        "net_down_v": net_down, "net_up_v": net_up,
        "disk_read": fmt_bytes(disk_r, lang, rate=True), "disk_write": fmt_bytes(disk_w, lang, rate=True),
        "temp": temp, "temp_text": dash if temp is None else f"{temp:.0f}°", "temp_src": temp_src,
        "battery": battery["pct"] if battery else None, "battery_text": battery_text,
        "battery_plugged": bool(battery and battery.get("plugged")),
        "uptime": cur.get("uptime"), "host": static.get("host") or "", "os": static.get("os") or "",

        "cores": [{"label": str(i + 1), "value": v} for i, v in enumerate(cores[:64])],
        "swap": swap_pct, "swap_text": pct_text(swap_pct), "swap_caption": swap_caption,
        "vram": vram_pct, "vram_text": pct_text(vram_pct),
        "vram_caption": (f"{fmt_bytes(gpu_mem_used, lang)}" + (f" / {fmt_bytes(gpu_mem_total, lang)}" if gpu_mem_total else ""))
        if gpu_mem_used is not None else t("нет данных", "no data"),
        "battery_caption": battery_caption,
        "nets": nets, "nets_bits": nets_bits,
        "net_down_bits": fmt_bits(net_down, lang), "net_up_bits": fmt_bits(net_up, lang),
        "disks_list": disks_list, "disks_rows": disks_rows,
        "disk_io_v": (disk_r or 0) + (disk_w or 0) if disk_r is not None or disk_w is not None else None,
        "temps_rows": temp_rows(False), "temps_rows_f": temp_rows(True),
        "temps_meters": temp_meters(False), "temps_meters_f": temp_meters(True), "temps_note": temps_note,
        "fans_rows": [{"title": label, "value": f"{rpm:.0f} " + t("об/мин", "rpm")} for label, rpm in fans_all[:8]],
        "fans_note": fans_note,
        "gpu_temp_text_f": dash if nv_temp is None else f"{_to_f(nv_temp):.0f}°",
        "temp_text_f": dash if temp is None else f"{_to_f(temp):.0f}°",
        "uptime_text": fmt_duration(cur.get("uptime"), lang),
    }

_PROCS: Dict[str, Any] = {"objs": {}, "at": 0.0, "cache": None, "key": None}

def top_processes(count: int = 5, sort: str = "cpu", *, lang: str = "ru") -> Dict[str, Any]:
    """Самые активные процессы: имя, доля CPU (от всего процессора) и память.

    Нужен psutil (он в requirements и в сборке). Доля CPU считается между двумя вызовами, поэтому
    самый первый замер показывает только память — это честно, а не «0 %» у всех."""
    t = (lambda ru, en: en if lang == "en" else ru)
    ps = _psutil()
    if ps is None:
        return {"procs": [], "procs_note": t("Список процессов требует модуль psutil.", "The process list needs psutil.")}
    count = max(1, min(20, int(count or 5)))
    with _LOCK:
        now = time.monotonic()
        key = (count, sort, lang)
        if _PROCS["cache"] is not None and _PROCS["key"] == key and now - _PROCS["at"] < CACHE_SECONDS:
            return dict(_PROCS["cache"])
        cores = os.cpu_count() or 1
        seen = {}
        rows = []
        primed = bool(_PROCS["objs"])
        try:
            for proc in ps.process_iter(["pid", "name", "memory_info"]):
                pid = proc.info.get("pid")
                obj = _PROCS["objs"].get(pid) or proc
                try:
                    cpu = obj.cpu_percent(None) / cores
                except Exception:  # noqa: BLE001 — процесс завершился или нет прав
                    continue
                seen[pid] = obj
                mem = proc.info.get("memory_info")
                name = str(proc.info.get("name") or "")
                if not name or pid in (0,) or name.lower() in ("system idle process", "idle"):
                    continue
                rows.append((name, cpu, float(mem.rss) if mem else 0.0))
        except Exception as exc:  # noqa: BLE001
            log.info("system: process list failed: %s", exc)
        _PROCS["objs"] = seen

        merged: Dict[str, List[float]] = {}
        for name, cpu, rss in rows:
            slot = merged.setdefault(name, [0.0, 0.0, 0])
            slot[0] += cpu
            slot[1] += rss
            slot[2] += 1
        order = sorted(merged.items(), key=lambda kv: -(kv[1][1] if sort == "mem" or not primed else kv[1][0]))
        out = []
        for name, (cpu, rss, n) in order[:count]:
            label = name[:-4] if name.lower().endswith(".exe") else name
            out.append({"title": (label + (f" ×{n}" if n > 1 else ""))[:40],
                        "value": (f"{cpu:.1f} %".replace(".", "," if lang != "en" else ".") if primed else fmt_bytes(rss, lang)),
                        "subtitle": fmt_bytes(rss, lang) if primed else ""})
        result = {"procs": out, "procs_note": "" if out else t("Нет данных о процессах.", "No process data.")}
        _PROCS["cache"], _PROCS["at"], _PROCS["key"] = result, time.monotonic(), key
        return dict(result)
