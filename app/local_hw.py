"""Hardware detection for local models (Round 33).

``detect()`` describes the computer as the local model wizard and the runtime need it:

* OS, CPU (name, logical threads, physical cores, AVX/AVX2/AVX-512), RAM (total, available,
  type and speed when Windows reports them), free disk space in the models folder;
* GPUs with exact dedicated memory: DXGI on Windows (64-bit ``DedicatedVideoMemory``, no
  PowerShell), the display driver registry (``qwMemorySize``) and CIM as fallbacks, and
  ``nvidia-smi`` everywhere (free VRAM, driver, compute capability);
* estimated memory bandwidth of every device (what generation speed depends on).

Every probe is best effort and guarded: a missing tool, a non-Windows system or a timeout just
leaves the field empty. ``MEMUAI_HW_MOCK`` (inline JSON or a path to a JSON file) replaces the
detected values — used by tests and for planning on another computer; ``detect(mock=…)`` does the
same in code. Nothing here needs administrator rights.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
GB = 1024 ** 3

_cache: Dict[str, Any] = {}
_cache_lock = threading.Lock()

runner: Optional[Callable[..., Any]] = None

def _run(args: List[str], timeout: float = 10.0) -> str:
    try:
        if runner is not None:
            result = runner(args, timeout=timeout)
        else:
            result = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                                    creationflags=_NO_WINDOW)
        return str(getattr(result, "stdout", "") or "")
    except Exception:
        return ""

GPU_BANDWIDTH = [
    (r"rtx\s*pro\s*6000|rtx\s*6000\s*ada", 960), (r"\bh100\b", 3350), (r"\ba100\b", 1555), (r"\bl40s?\b", 864),
    (r"rtx\s*5090", 1792), (r"rtx\s*5080", 960), (r"rtx\s*5070\s*ti", 896), (r"rtx\s*5070", 672),
    (r"rtx\s*5060\s*ti", 448), (r"rtx\s*5060", 448), (r"rtx\s*5050", 320),
    (r"rtx\s*4090", 1008), (r"rtx\s*4080\s*super", 736), (r"rtx\s*4080", 717), (r"rtx\s*4070\s*ti\s*super", 672),
    (r"rtx\s*4070\s*ti", 504), (r"rtx\s*4070\s*super", 504), (r"rtx\s*4070", 504), (r"rtx\s*4060\s*ti", 288),
    (r"rtx\s*4060", 272), (r"rtx\s*4050", 192),
    (r"rtx\s*3090\s*ti", 1008), (r"rtx\s*3090", 936), (r"rtx\s*3080\s*ti", 912), (r"rtx\s*3080", 760),
    (r"rtx\s*3070\s*ti", 608), (r"rtx\s*3070", 448), (r"rtx\s*3060\s*ti", 448), (r"rtx\s*3060", 360), (r"rtx\s*3050", 224),
    (r"rtx\s*2080\s*ti", 616), (r"rtx\s*2080", 448), (r"rtx\s*2070", 448), (r"rtx\s*2060", 336),
    (r"titan\s*rtx", 672), (r"gtx\s*1080\s*ti", 484), (r"gtx\s*1080", 320), (r"gtx\s*1070", 256),
    (r"gtx\s*16[56]0", 192), (r"gtx\s*1060", 192), (r"gtx\s*1050", 112),
    (r"rx\s*9070\s*xt", 640), (r"rx\s*9070", 640), (r"rx\s*9060\s*xt", 320), (r"rx\s*7900\s*xtx", 960),
    (r"rx\s*7900\s*xt", 800), (r"rx\s*7900\s*gre", 576), (r"rx\s*7800\s*xt", 624), (r"rx\s*7700\s*xt", 432),
    (r"rx\s*7600", 288), (r"rx\s*6950", 576), (r"rx\s*69[05]0\s*xt", 512), (r"rx\s*6800", 512), (r"rx\s*6750", 432),
    (r"rx\s*6700", 384), (r"rx\s*6650|rx\s*6600", 224), (r"rx\s*6500", 144), (r"radeon\s*vii", 1024),
    (r"rx\s*5700", 448), (r"rx\s*580|rx\s*590", 256),
    (r"arc\s*b580", 456), (r"arc\s*b570", 380), (r"arc\s*a770", 560), (r"arc\s*a750", 512), (r"arc\s*a580", 512),
    (r"arc\s*a380", 186),
]

_INTEGRATED_RE = re.compile(
    r"(?i)\b(uhd|hd graphics|iris|intel\(r\) graphics|intel graphics|arc\(tm\) graphics|"
    r"radeon\(tm\) graphics|radeon graphics|vega \d+ graphics|radeon \d{3}m|adreno|"
    r"microsoft basic|basic display|apple m\d)")
_IGNORED_RE = re.compile(r"(?i)basic display|basic render|remote|virtual|parsec|mirror|citrix|vmware|hyper-v|"
                         r"spacedesk|displaylink|idd")

def vendor_of(name: str) -> str:
    low = str(name or "").lower()
    if re.search(r"nvidia|geforce|rtx|quadro|tesla|titan|gtx", low):
        return "nvidia"
    if re.search(r"amd|radeon|\brx\s*\d", low):
        return "amd"
    if re.search(r"intel|arc|iris|uhd", low):
        return "intel"
    if "apple" in low:
        return "apple"
    return "other"

def gpu_bandwidth(name: str, vram: int = 0) -> int:
    """Memory bandwidth of a GPU in GB/s: a known model, else a guess from its VRAM size."""
    low = str(name or "").lower()
    for pattern, value in GPU_BANDWIDTH:
        if re.search(pattern, low):

            return int(value * (0.62 if re.search(r"laptop|mobile|max-q|\bm\b", low) else 1.0))
    gb = vram / GB if vram else 0
    if gb >= 20:
        return 700
    if gb >= 14:
        return 448
    if gb >= 11:
        return 360
    if gb >= 7:
        return 272
    if gb >= 5:
        return 192
    return 128 if gb else 0

def ram_bandwidth(ram_type: str = "", speed: int = 0, channels: int = 0) -> int:
    """Practical system RAM bandwidth in GB/s (dual channel unless told otherwise)."""
    kind = str(ram_type or "").upper()
    channels = channels if channels in (1, 2, 4, 8) else 2
    if not speed:
        speed = {"DDR5": 5600, "LPDDR5": 6400, "LPDDR5X": 7500, "DDR4": 3200, "LPDDR4X": 4266, "DDR3": 1600}.get(kind, 0)
    if not speed:
        return 50
    theoretical = speed * 8 * channels / 1000
    return int(theoretical * 0.75)

def _os_info() -> Dict[str, Any]:
    name = {"win32": "Windows", "darwin": "macOS"}.get(sys.platform, platform.system() or sys.platform)
    release = platform.release()
    version = platform.version()
    if sys.platform == "win32":
        try:
            build = int(version.split(".")[-1])
            if build >= 22000:
                release = "11"
        except (ValueError, IndexError):
            pass
    return {"name": name, "release": release, "version": version, "machine": platform.machine(),
            "label": f"{name} {release}".strip()}

def _cpu_info() -> Dict[str, Any]:
    info: Dict[str, Any] = {"name": "", "threads": os.cpu_count() or 0, "cores": 0,
                            "avx": None, "avx2": None, "avx512": None, "fma": None}
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                info["name"] = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except Exception:
            pass
        try:
            import ctypes
            present = ctypes.windll.kernel32.IsProcessorFeaturePresent

            info["avx"], info["avx2"], info["avx512"] = (bool(present(39)), bool(present(40)), bool(present(41)))
        except Exception:
            pass
        info["cores"] = _win_physical_cores()
    elif sys.platform.startswith("linux"):
        try:
            text = Path("/proc/cpuinfo").read_text(errors="replace")
            name = re.search(r"(?m)^model name\s*:\s*(.+)$", text)
            info["name"] = name.group(1).strip() if name else ""
            flags = re.search(r"(?m)^flags\s*:\s*(.+)$", text)
            words = set(flags.group(1).split()) if flags else set()
            if words:
                info.update({"avx": "avx" in words, "avx2": "avx2" in words, "avx512": "avx512f" in words,
                             "fma": "fma" in words})
            cores = set(re.findall(r"(?ms)^physical id\s*:\s*(\d+).*?^core id\s*:\s*(\d+)", text))
            info["cores"] = len(cores)
        except OSError:
            pass
    elif sys.platform == "darwin":
        info["name"] = _run(["sysctl", "-n", "machdep.cpu.brand_string"], 5).strip()
        try:
            info["cores"] = int(_run(["sysctl", "-n", "hw.physicalcpu"], 5).strip() or 0)
        except ValueError:
            pass
    if not info["cores"]:

        threads = int(info["threads"] or 0)
        info["cores"] = max(1, threads // 2) if threads > 2 else threads
    return info

def _win_physical_cores() -> int:
    """Physical cores via GetLogicalProcessorInformationEx(RelationProcessorCore) — no PowerShell."""
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        length = wintypes.DWORD(0)
        kernel32.GetLogicalProcessorInformationEx(0, None, ctypes.byref(length))
        if not length.value:
            return 0
        buffer = ctypes.create_string_buffer(length.value)
        if not kernel32.GetLogicalProcessorInformationEx(0, buffer, ctypes.byref(length)):
            return 0
        count, offset = 0, 0
        while offset < length.value:
            relationship, size = ctypes.c_uint32.from_buffer(buffer, offset).value, \
                ctypes.c_uint32.from_buffer(buffer, offset + 4).value
            if relationship == 0:
                count += 1
            if size <= 0:
                break
            offset += size
        return count
    except Exception:
        return 0

def _ram_info() -> Dict[str, Any]:
    info: Dict[str, Any] = {"total": 0, "available": 0, "type": "", "speed": 0, "modules": 0}
    if sys.platform == "win32":
        try:
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                info["total"], info["available"] = int(status.ullTotalPhys), int(status.ullAvailPhys)
        except Exception:
            pass
    elif sys.platform.startswith("linux"):
        try:
            text = Path("/proc/meminfo").read_text()
            total = re.search(r"(?m)^MemTotal:\s+(\d+)", text)
            available = re.search(r"(?m)^MemAvailable:\s+(\d+)", text)
            info["total"] = int(total.group(1)) * 1024 if total else 0
            info["available"] = int(available.group(1)) * 1024 if available else 0
        except OSError:
            pass
    if not info["total"]:
        try:
            info["total"] = int(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
        except (ValueError, OSError, AttributeError):
            pass
    return info

_SMBIOS_RAM = {20: "DDR", 21: "DDR2", 24: "DDR3", 26: "DDR4", 27: "LPDDR", 28: "LPDDR2", 29: "LPDDR3",
               30: "LPDDR4", 34: "DDR5", 35: "LPDDR5"}

_CIM_SCRIPT = (
    "$ErrorActionPreference='SilentlyContinue';"
    "$v=Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM,DriverVersion;"
    "$m=Get-CimInstance Win32_PhysicalMemory | Select-Object Speed,ConfiguredClockSpeed,SMBIOSMemoryType,Capacity;"
    "@{video=@($v);memory=@($m)} | ConvertTo-Json -Compress -Depth 3"
)

def _win_cim() -> Dict[str, Any]:
    text = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _CIM_SCRIPT], 15)
    try:
        data = json.loads(text or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}

def _win_dxgi() -> List[Dict[str, Any]]:
    """Adapters from DXGI: exact 64-bit dedicated memory, vendor id, software adapters skipped."""
    try:
        import ctypes
        from ctypes import wintypes

        class LUID(ctypes.Structure):
            _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

        class DESC1(ctypes.Structure):
            _fields_ = [("Description", wintypes.WCHAR * 128), ("VendorId", wintypes.UINT),
                        ("DeviceId", wintypes.UINT), ("SubSysId", wintypes.UINT), ("Revision", wintypes.UINT),
                        ("DedicatedVideoMemory", ctypes.c_size_t), ("DedicatedSystemMemory", ctypes.c_size_t),
                        ("SharedSystemMemory", ctypes.c_size_t), ("AdapterLuid", LUID), ("Flags", wintypes.UINT)]

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                        ("Data4", ctypes.c_ubyte * 8)]

        iid = GUID(0x770AAE78, 0xF26F, 0x4DBA, (ctypes.c_ubyte * 8)(0xA8, 0x29, 0x25, 0x3C, 0x83, 0xD1, 0xB3, 0x87))
        dxgi = ctypes.WinDLL("dxgi")
        factory = ctypes.c_void_p()
        if dxgi.CreateDXGIFactory1(ctypes.byref(iid), ctypes.byref(factory)) != 0 or not factory:
            return []

        def method(obj, index, *argtypes):
            vtable = ctypes.cast(ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
            return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtable[index])

        out = []
        try:
            for index in range(16):
                adapter = ctypes.c_void_p()

                if method(factory, 12, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p))(
                        factory, index, ctypes.byref(adapter)) != 0:
                    break
                try:
                    desc = DESC1()

                    if method(adapter, 10, ctypes.POINTER(DESC1))(adapter, ctypes.byref(desc)) == 0 \
                            and not (desc.Flags & 2):
                        out.append({"name": desc.Description.strip(), "vram": int(desc.DedicatedVideoMemory),
                                    "shared": int(desc.SharedSystemMemory), "vendor_id": int(desc.VendorId)})
                finally:
                    method(adapter, 2)(adapter)
        finally:
            method(factory, 2)(factory)
        return out
    except Exception:
        return []

def _win_registry_vram() -> Dict[str, int]:
    try:
        import winreg
    except ImportError:
        return {}
    out: Dict[str, int] = {}
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as root:
            for index in range(64):
                try:
                    sub = winreg.EnumKey(root, index)
                except OSError:
                    break
                if not sub.isdigit():
                    continue
                try:
                    with winreg.OpenKey(root, sub) as key:
                        name = str(winreg.QueryValueEx(key, "DriverDesc")[0]).strip()
                        memory = winreg.QueryValueEx(key, "HardwareInformation.qwMemorySize")[0]
                        if isinstance(memory, bytes):
                            memory = int.from_bytes(memory[:8], "little")
                        if name and int(memory) > 0:
                            out[name] = max(out.get(name, 0), int(memory))
                except OSError:
                    continue
    except OSError:
        return {}
    return out

def _nvidia_smi() -> List[Dict[str, Any]]:
    candidates = ["nvidia-smi"]
    if sys.platform == "win32":
        candidates.append(str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe"))
    if runner is None and not any(shutil.which(c) or Path(c).is_file() for c in candidates):
        return []
    for command in candidates:
        text = _run([command, "--query-gpu=name,memory.total,memory.free,driver_version,compute_cap",
                     "--format=csv,noheader,nounits"], 8)
        rows = []
        for line in text.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 3 or not parts[0]:
                continue
            try:
                rows.append({"name": parts[0], "vram": int(float(parts[1])) * 1024 * 1024,
                             "vram_free": int(float(parts[2])) * 1024 * 1024,
                             "driver": parts[3] if len(parts) > 3 else "",
                             "compute": parts[4] if len(parts) > 4 else ""})
            except ValueError:
                continue
        if rows:
            return rows
    return []

def _linux_amd() -> List[Dict[str, Any]]:
    out = []
    for card in sorted(Path("/sys/class/drm").glob("card[0-9]*")):
        total = card / "device" / "mem_info_vram_total"
        try:
            vram = int(total.read_text().strip())
        except (OSError, ValueError):
            continue
        if vram < 1024 ** 3:
            continue
        used = 0
        try:
            used = int((card / "device" / "mem_info_vram_used").read_text().strip())
        except (OSError, ValueError):
            pass
        out.append({"name": "AMD Radeon GPU", "vram": vram, "vram_free": max(0, vram - used)})
    return out

def _gpus(cim: Dict[str, Any]) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []

    def add(row: Dict[str, Any], source: str) -> None:
        name = str(row.get("name") or "").strip()
        if not name or _IGNORED_RE.search(name):
            return
        key = re.sub(r"\W+", "", name.lower())
        for item in found:
            if re.sub(r"\W+", "", item["name"].lower()) == key:
                for field in ("vram", "vram_free", "driver", "compute"):
                    if row.get(field) and (field != "vram" or source in ("nvidia-smi", "dxgi") or not item.get("vram")):
                        item[field] = row[field]
                item["sources"].append(source)
                return
        found.append({"name": name, "vram": int(row.get("vram") or 0), "vram_free": int(row.get("vram_free") or 0),
                      "driver": str(row.get("driver") or ""), "compute": str(row.get("compute") or ""),
                      "sources": [source]})

    if sys.platform == "win32":
        for row in _win_dxgi():
            add(row, "dxgi")
        registry = _win_registry_vram()
        for row in cim.get("video") or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("Name") or "").strip()

            add({"name": name, "vram": registry.get(name) or int(row.get("AdapterRAM") or 0),
                 "driver": str(row.get("DriverVersion") or "")}, "cim")
    elif sys.platform.startswith("linux"):
        for row in _linux_amd():
            add(row, "sysfs")
    for row in _nvidia_smi():
        add(row, "nvidia-smi")
    for gpu in found:
        gpu["vendor"] = vendor_of(gpu["name"])
        gpu["integrated"] = bool(_INTEGRATED_RE.search(gpu["name"])) or (gpu["vendor"] != "nvidia" and gpu["vram"] < 1024 ** 3)
        gpu["bandwidth"] = gpu_bandwidth(gpu["name"], gpu["vram"]) if not gpu["integrated"] else 0
    found.sort(key=lambda gpu: (gpu["integrated"], -gpu["vram"]))
    return found

def _ram_details(cim: Dict[str, Any]) -> Dict[str, Any]:
    modules = [row for row in cim.get("memory") or [] if isinstance(row, dict)]
    if not modules:
        return {}
    kinds = [_SMBIOS_RAM.get(int(row.get("SMBIOSMemoryType") or 0), "") for row in modules]
    speeds = [int(row.get("ConfiguredClockSpeed") or row.get("Speed") or 0) for row in modules]
    kind = next((k for k in kinds if k), "")
    speed = max(speeds) if speeds else 0
    if not kind and speed:
        kind = "DDR5" if speed >= 4400 else "DDR4" if speed >= 2000 else "DDR3"
    return {"type": kind, "speed": speed, "modules": len(modules)}

def _disk(path: Optional[Path]) -> Dict[str, Any]:
    target = path
    while target is not None and not target.exists():
        target = target.parent if target.parent != target else None
    try:
        usage = shutil.disk_usage(str(target or Path.home()))
        return {"path": str(path or ""), "free": int(usage.free), "total": int(usage.total)}
    except OSError:
        return {"path": str(path or ""), "free": 0, "total": 0}

def _mock() -> Optional[Dict[str, Any]]:
    raw = os.environ.get("MEMUAI_HW_MOCK", "").strip()
    if not raw:
        return None
    try:
        if raw.startswith("{"):
            data = json.loads(raw)
        else:
            data = json.loads(Path(raw).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None

def finish(info: Dict[str, Any]) -> Dict[str, Any]:
    """Fill derived fields (vendor, bandwidth, backend…) of a detected or user-edited profile."""
    info = json.loads(json.dumps(info))
    ram = info.setdefault("ram", {})
    ram.setdefault("total", 0)
    ram.setdefault("available", 0)
    ram["bandwidth"] = int(ram.get("bandwidth") or ram_bandwidth(ram.get("type", ""), int(ram.get("speed") or 0)))
    cpu = info.setdefault("cpu", {})
    cpu.setdefault("threads", os.cpu_count() or 0)
    cpu.setdefault("cores", max(1, int(cpu.get("threads") or 2) // 2))
    for key in ("avx", "avx2", "avx512"):
        cpu.setdefault(key, None)
    gpus = []
    for gpu in info.get("gpus") or []:
        if not isinstance(gpu, dict) or not str(gpu.get("name") or "").strip():
            continue
        gpu = dict(gpu)
        gpu["vram"] = int(gpu.get("vram") or 0)
        gpu["vendor"] = gpu.get("vendor") or vendor_of(gpu["name"])
        if "integrated" not in gpu:
            gpu["integrated"] = bool(_INTEGRATED_RE.search(gpu["name"])) or (gpu["vendor"] != "nvidia" and gpu["vram"] < GB)
        gpu["bandwidth"] = int(gpu.get("bandwidth") or (0 if gpu["integrated"] else gpu_bandwidth(gpu["name"], gpu["vram"])))
        gpus.append(gpu)
    gpus.sort(key=lambda gpu: (bool(gpu["integrated"]), -gpu["vram"]))
    info["gpus"] = gpus
    main = next((gpu for gpu in gpus if not gpu["integrated"] and gpu["vram"] >= GB), None)
    info["main_gpu"] = main
    if main and main["vendor"] == "nvidia":
        backend = "cuda"
    elif main and main["vendor"] in ("amd", "intel"):
        backend = "vulkan"
    else:
        backend = "cpu"
    info["backend"] = backend
    info.setdefault("os", _os_info())
    info.setdefault("disk", {"free": 0, "total": 0, "path": ""})
    return info

def detect(refresh: bool = False, mock: Optional[Dict[str, Any]] = None,
           models_path: Optional[Path] = None) -> Dict[str, Any]:
    """The hardware profile (cached for 5 minutes; free memory changes, the parts do not)."""
    override = mock if mock is not None else _mock()
    if override is not None:
        data = finish(override)
        data["mocked"] = True
        return data
    with _cache_lock:
        if _cache and not refresh and time.time() - _cache.get("_at", 0) < 300:
            data = dict(_cache)
            data.pop("_at", None)
            return data
    started = time.time()
    cim = _win_cim() if sys.platform == "win32" else {}
    ram = _ram_info()
    ram.update({key: value for key, value in _ram_details(cim).items() if value})
    info = {"os": _os_info(), "cpu": _cpu_info(), "ram": ram, "gpus": _gpus(cim),
            "disk": _disk(models_path)}
    data = finish(info)
    data["mocked"] = False
    data["probe_ms"] = int((time.time() - started) * 1000)
    with _cache_lock:
        _cache.clear()
        _cache.update(data)
        _cache["_at"] = time.time()
    return data

def legacy(profile: Dict[str, Any]) -> Dict[str, Any]:
    """The old ``local_models.hardware()`` shape: {ram, gpus [{name, vram}], cpu_threads} + new fields."""
    gpus = [{"name": gpu["name"], "vram": int(gpu.get("vram") or 0), "vram_free": int(gpu.get("vram_free") or 0),
             "vendor": gpu.get("vendor", ""), "integrated": bool(gpu.get("integrated"))}
            for gpu in profile.get("gpus") or []]
    cpu = profile.get("cpu") or {}
    return {"ram": int((profile.get("ram") or {}).get("total") or 0), "gpus": gpus,
            "cpu_threads": int(cpu.get("threads") or 0), "cpu_cores": int(cpu.get("cores") or 0),
            "cpu_name": str(cpu.get("name") or ""), "avx2": cpu.get("avx2"),
            "backend": profile.get("backend", "cpu")}
