# -*- coding: utf-8 -*-
"""System utilities: PC info, disks, big files, folder sizes, duplicates, empty folders, bulk rename, processes,
listening ports, startup programs, installed programs, temp cleanup, Windows tweaks (current user only, reversible),
power timer, DNS flush, Explorer restart, hosts, environment, battery, saved Wi-Fi, auto-clicker, auto-typing and
keep-awake. Everything that changes the system is ``danger`` (or ``danger_if``) — the chat asks the user first —
and deleting/renaming defaults to a preview."""
from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from . import jobs
from .core import O, UtilError, as_int, human_size, table, utility

WIN = sys.platform == "win32"
NO_WINDOW = 0x08000000 if WIN else 0
WALK_LIMIT = 1_500_000
WALK_SECONDS = 120.0

def _run(cmd: List[str], timeout: float = 30.0) -> Tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
    except FileNotFoundError:
        raise UtilError(f"Команда {cmd[0]} не найдена", f"The {cmd[0]} command is not found")
    except subprocess.TimeoutExpired:
        raise UtilError(f"{cmd[0]} не ответила за {int(timeout)} с", f"{cmd[0]} did not answer in {int(timeout)} s")
    raw = proc.stdout + proc.stderr
    for enc in (("cp866", "utf-8") if WIN else ("utf-8",)):
        try:
            return proc.returncode, raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return proc.returncode, raw.decode("utf-8", "replace")

def _need_win(ctx) -> None:
    if not WIN:
        raise UtilError("Эта утилита работает только в Windows", "This utility works on Windows only")

def _psutil():
    try:
        import psutil
        return psutil
    except ImportError:
        raise UtilError("Библиотека psutil недоступна", "psutil is not available")

def _drives(psutil, limit: int = 0):
    """Real drives with their usage: no tiny system mounts, no file bind-mounts, no duplicates of one volume."""
    seen = set()
    parts = psutil.disk_partitions(all=False)
    for part in parts[:limit] if limit else parts:
        try:
            u = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        if not WIN:
            if u.total < 1_000_000_000 or part.mountpoint.startswith(("/snap", "/boot/efi")) or not os.path.isdir(part.mountpoint):
                continue
            key = (part.device, u.total)
            if key in seen:
                continue
            seen.add(key)
        yield part, u

def _folder(ctx, default: str = "downloads") -> Path:
    raw = str(ctx.text or "").strip().strip('"')
    if not raw:
        if default == "home":
            return Path.home()
        try:
            from ..quick_apps import downloads_folder
            return Path(downloads_folder())
        except Exception:  # noqa: BLE001
            return Path.home() / "Downloads"
    path = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not path.exists():
        raise UtilError(f"Папка не найдена: {path}", f"Folder not found: {path}")
    if not path.is_dir():
        raise UtilError(f"Это не папка: {path}", f"Not a folder: {path}")
    return path

def _walk(root: Path, files: bool = True, dirs: bool = False) -> Iterator[Tuple[str, os.stat_result, bool]]:
    """``(path, stat, is_dir)`` under *root* without following links/junctions; unreadable folders are skipped."""
    stack = [str(root)]
    seen = 0
    started = time.monotonic()
    while stack:
        top = stack.pop()
        try:
            with os.scandir(top) as it:
                for entry in it:
                    seen += 1
                    if seen > WALK_LIMIT or time.monotonic() - started > WALK_SECONDS:
                        raise _WalkLimit()
                    try:
                        if entry.is_symlink() or (WIN and entry.is_dir(follow_symlinks=False) and _is_junction(entry.path)):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                            if dirs:
                                yield entry.path, entry.stat(follow_symlinks=False), True
                        elif files and entry.is_file(follow_symlinks=False):
                            yield entry.path, entry.stat(follow_symlinks=False), False
                    except OSError:
                        continue
        except OSError:
            continue

class _WalkLimit(Exception):
    pass

def _is_junction(path: str) -> bool:
    try:
        return bool(getattr(os.path, "isjunction", lambda p: False)(path))
    except OSError:
        return False

def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")

# ── information ──────────────────────────────────────────────────────────────────────────────────────────────────────
@utility("sys.info", "system", "Сведения о компьютере", "Computer info",
         "Система, процессор, ядра, память, видеокарта, диски, время работы, экран — одной таблицей, удобно скопировать в поддержку.",
         "OS, CPU, cores, memory, graphics, disks, uptime, screen — in one table, handy to paste into a support request.",
         input="none", ai_default=True, keys="компьютер характеристики система процессор память видеокарта pc specs system info cpu ram gpu")
def sys_info(ctx):
    psutil = _psutil()
    uname = platform.uname()
    rows = [[ctx.tr("Компьютер", "Computer"), uname.node], [ctx.tr("Система", "OS"), _os_name()], [ctx.tr("Архитектура", "Architecture"), uname.machine]]
    cpu = _cpu_name()
    rows.append([ctx.tr("Процессор", "CPU"), cpu])
    try:
        freq = psutil.cpu_freq()
        rows.append([ctx.tr("Ядра / потоки", "Cores / threads"), f"{psutil.cpu_count(False)} / {psutil.cpu_count(True)}" + (f" · {freq.max / 1000:.2f} " + ctx.tr("ГГц", "GHz") if freq and freq.max else "")])
        rows.append([ctx.tr("Загрузка ЦП", "CPU load"), f"{psutil.cpu_percent(interval=0.4):.0f}%"])
    except Exception:  # noqa: BLE001
        pass
    vm = psutil.virtual_memory()
    rows.append([ctx.tr("Память", "Memory"), f"{human_size(vm.total, ctx.en)} · {ctx.tr('занято', 'used')} {vm.percent:.0f}% · {ctx.tr('свободно', 'free')} {human_size(vm.available, ctx.en)}"])
    gpu = _gpu_names()
    if gpu:
        rows.append([ctx.tr("Видеокарта", "Graphics"), gpu])
    for part, u in _drives(psutil, 12):
        rows.append([ctx.tr("Диск ", "Disk ") + part.mountpoint, f"{human_size(u.total, ctx.en)} · {ctx.tr('свободно', 'free')} {human_size(u.free, ctx.en)} ({100 - u.percent:.0f}%) · {part.fstype}"])
    boot = psutil.boot_time()
    up = int(time.time() - boot)
    rows.append([ctx.tr("Работает", "Uptime"), ctx.tr(f"{up // 86400} д {up % 86400 // 3600} ч {up % 3600 // 60} мин", f"{up // 86400} d {up % 86400 // 3600} h {up % 3600 // 60} min")])
    screen = _screen(ctx.en)
    if screen:
        rows.append([ctx.tr("Экран", "Screen"), screen])
    try:
        bat = psutil.sensors_battery()
        if bat:
            rows.append([ctx.tr("Батарея", "Battery"), f"{bat.percent:.0f}%" + (ctx.tr(" · заряжается", " · charging") if bat.power_plugged else "")])
    except Exception:  # noqa: BLE001
        pass
    rows.append(["Python", platform.python_version()])
    try:
        from .. import config
        rows.append(["MemuAI", str(getattr(config, "APP_VERSION", "") or getattr(config, "VERSION", ""))])
    except Exception:  # noqa: BLE001
        pass
    return {"info": [r for r in rows if r[1]]}

def _os_name() -> str:
    if WIN:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as key:
                name = winreg.QueryValueEx(key, "ProductName")[0]
                build = int(winreg.QueryValueEx(key, "CurrentBuildNumber")[0])
                try:
                    display = winreg.QueryValueEx(key, "DisplayVersion")[0]
                except OSError:
                    display = ""
            if build >= 22000:
                name = name.replace("Windows 10", "Windows 11")
            return f"{name} {display} (build {build})".replace("  ", " ")
        except Exception:  # noqa: BLE001
            return f"Windows {platform.release()} ({platform.version()})"
    try:
        info = platform.freedesktop_os_release()
        return f"{info.get('PRETTY_NAME', 'Linux')} · kernel {platform.release()}"
    except Exception:  # noqa: BLE001
        return f"{platform.system()} {platform.release()}"

def _cpu_name() -> str:
    if WIN:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except Exception:  # noqa: BLE001
            return platform.processor()
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()

def _gpu_names() -> str:
    if WIN:
        try:
            _code, out = _run(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController).Name"], 20)
            return ", ".join(l.strip() for l in out.splitlines() if l.strip())
        except UtilError:
            return ""
    try:
        _code, out = _run(["sh", "-c", "lspci 2>/dev/null | grep -Ei 'vga|3d|display' | cut -d: -f3"], 10)
        return ", ".join(l.strip() for l in out.splitlines() if l.strip())
    except UtilError:
        return ""

def _screen(en: bool = False) -> str:
    if WIN:
        try:
            import ctypes
            user32 = ctypes.windll.user32
            try:
                user32.SetProcessDPIAware()
            except Exception:  # noqa: BLE001
                pass
            count = int(user32.GetSystemMetrics(80) or 1)
            size = f"{user32.GetSystemMetrics(0)}×{user32.GetSystemMetrics(1)}"
            if count <= 1:
                return size
            if en:
                return f"{size} · {count} monitors"
            tail = count % 100
            word = "мониторов" if 11 <= tail <= 14 else {1: "монитор", 2: "монитора", 3: "монитора", 4: "монитора"}.get(count % 10, "мониторов")
            return f"{size} · {count} {word}"
        except Exception:  # noqa: BLE001
            return ""
    return ""

@utility("sys.disks", "system", "Диски и место", "Disks and space",
         "Все диски: объём, занято, свободно, файловая система — и предупреждение, если места почти не осталось.",
         "All drives: size, used, free, file system — with a warning when space is running out.",
         input="none", ai_default=True, keys="диски место свободно память ssd hdd disk space free drives")
def sys_disks(ctx):
    psutil = _psutil()
    rows, warn = [], []
    for part, u in _drives(psutil):
        bar = "█" * round(u.percent / 10) + "░" * (10 - round(u.percent / 10))
        rows.append([part.mountpoint, part.fstype, human_size(u.total, ctx.en), human_size(u.used, ctx.en), human_size(u.free, ctx.en), f"{bar} {u.percent:.0f}%"])
        if u.percent >= 90:
            warn.append(part.mountpoint)
    note = ctx.tr(f"Почти нет места: {', '.join(warn)} — попробуйте «Большие файлы» и «Очистка временных файлов»",
                  f"Almost full: {', '.join(warn)} — try “Big files” and “Temp cleanup”") if warn else ""
    return {"table": table([ctx.tr("Диск", "Drive"), ctx.tr("ФС", "FS"), ctx.tr("Объём", "Size"), ctx.tr("Занято", "Used"), ctx.tr("Свободно", "Free"), ctx.tr("Заполнено", "Full")], rows), "note": note}

@utility("sys.bigfiles", "system", "Большие файлы", "Big files",
         "Находит самые большие файлы в папке и подпапках — что занимает место. Ничего не удаляет.",
         "Finds the biggest files in a folder and its subfolders — what takes the space. Deletes nothing.",
         input="text", pick="folder", placeholder="C:\\Users\\Имя\\Downloads (пусто — «Загрузки»)", placeholder_en="C:\\Users\\Name\\Downloads (empty = Downloads)",
         opts=[O("top", "int", "Сколько показать", "How many", 50, min=5, max=1000), O("min", "int", "Не меньше, МБ", "At least, MB", 50, min=0, max=1_000_000),
               O("ext", "text", "Только расширения (mp4,zip)", "Only extensions (mp4,zip)", "")],
         keys="большие файлы место занимает поиск диск big files large space finder")
def sys_bigfiles(ctx):
    root = _folder(ctx)
    exts = {e.strip().lower().lstrip(".") for e in str(ctx.o["ext"] or "").split(",") if e.strip()}
    limit = ctx.o["min"] * 1024 * 1024
    found: List[Tuple[int, str, float]] = []
    total = count = 0
    partial = False
    try:
        for path, st, _d in _walk(root):
            count += 1
            total += st.st_size
            if st.st_size >= limit and (not exts or Path(path).suffix.lower().lstrip(".") in exts):
                found.append((st.st_size, path, st.st_mtime))
    except _WalkLimit:
        partial = True
    found.sort(reverse=True)
    rows = [[human_size(s, ctx.en), os.path.relpath(p, root), _when(m)] for s, p, m in found[:ctx.o["top"]]]
    note = ctx.tr(f"Просмотрено файлов: {count:,} · всего {human_size(total)}".replace(",", " "), f"Files scanned: {count:,} · {human_size(total, True)} in total")
    if partial:
        note += ctx.tr(" · остановлено по лимиту, показан не весь диск", " · stopped at the limit, the result is partial")
    if not rows:
        return {"text": ctx.tr("Файлов такого размера нет", "No files that big"), "note": note}
    return {"table": table([ctx.tr("Размер", "Size"), ctx.tr("Файл", "File"), ctx.tr("Изменён", "Modified")], rows), "note": f"{root} · {note}"}

@utility("sys.foldersize", "system", "Размер папок", "Folder sizes",
         "Показывает, сколько весит каждая подпапка (с вложенными) — как WinDirStat, но одной таблицей.",
         "Shows how much each subfolder weighs (with everything inside) — like WinDirStat, as one table.",
         input="text", pick="folder", placeholder="C:\\Users\\Имя", placeholder_en="C:\\Users\\Name",
         opts=[O("top", "int", "Сколько показать", "How many", 40, min=5, max=500)], keys="размер папок что занимает место папка folder size disk usage du windirstat")
def sys_foldersize(ctx):
    root = _folder(ctx, "home")
    sizes: Dict[str, List[int]] = {}
    loose = [0, 0]
    partial = False
    try:
        for path, st, _d in _walk(root):
            rel = os.path.relpath(path, root)
            head = rel.split(os.sep, 1)[0]
            if head == rel:
                loose[0] += st.st_size
                loose[1] += 1
                continue
            bucket = sizes.setdefault(head, [0, 0])
            bucket[0] += st.st_size
            bucket[1] += 1
    except _WalkLimit:
        partial = True
    total = sum(v[0] for v in sizes.values()) + loose[0]
    rows = sorted(([k, v[0], v[1]] for k, v in sizes.items()), key=lambda r: -r[1])[:ctx.o["top"]]
    if loose[1]:
        rows.append([ctx.tr("(файлы в самой папке)", "(files in the folder itself)"), loose[0], loose[1]])
    out = [[name, human_size(size, ctx.en), f"{size * 100 / total:.1f}%" if total else "", f"{n:,}".replace(",", " ")] for name, size, n in rows]
    return {"table": table([ctx.tr("Папка", "Folder"), ctx.tr("Размер", "Size"), "%", ctx.tr("Файлов", "Files")], out),
            "note": f"{root} · {human_size(total, ctx.en)}" + (ctx.tr(" · неполный подсчёт (лимит)", " · partial (limit)") if partial else "")}

@utility("sys.duplicates", "system", "Поиск дубликатов файлов", "Find duplicate files",
         "Находит одинаковые файлы (по содержимому, не по имени) и показывает, сколько места они съедают. Ничего не удаляет.",
         "Finds identical files (by content, not by name) and shows how much space they waste. Deletes nothing.",
         input="text", pick="folder", placeholder="D:\\Фото", placeholder_en="D:\\Photos",
         opts=[O("min", "int", "Не меньше, КБ", "At least, KB", 100, min=0, max=10_000_000)], keys="дубликаты одинаковые файлы копии duplicates duplicate finder same files")
def sys_duplicates(ctx):
    root = _folder(ctx)
    by_size: Dict[int, List[str]] = {}
    partial = False
    try:
        for path, st, _d in _walk(root):
            if st.st_size >= max(1, ctx.o["min"] * 1024):
                by_size.setdefault(st.st_size, []).append(path)
    except _WalkLimit:
        partial = True

    def digest(path: str, head: bool) -> str:
        h = hashlib.blake2b(digest_size=20)
        try:
            with open(path, "rb") as fh:
                if head:
                    h.update(fh.read(65536))
                else:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
        except OSError:
            return ""
        return h.hexdigest()
    groups: List[Tuple[int, List[str]]] = []
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        heads: Dict[str, List[str]] = {}
        for p in paths:
            heads.setdefault(digest(p, True), []).append(p)
        for key, same in heads.items():
            if not key or len(same) < 2:
                continue
            full: Dict[str, List[str]] = {}
            for p in same:
                full.setdefault(digest(p, size <= 65536) if size > 65536 else key, []).append(p)
            groups += [(size, g) for k, g in full.items() if k and len(g) > 1]
    groups.sort(key=lambda g: -g[0] * (len(g[1]) - 1))
    wasted = sum(size * (len(g) - 1) for size, g in groups)
    rows = []
    for n, (size, paths) in enumerate(groups[:300], 1):
        for k, p in enumerate(sorted(paths, key=len)):
            rows.append([str(n) if k == 0 else "", human_size(size, ctx.en) if k == 0 else "", os.path.relpath(p, root)])
    if not rows:
        return ctx.tr("Дубликатов не найдено", "No duplicates found")
    return {"table": table(["#", ctx.tr("Размер", "Size"), ctx.tr("Файл", "File")], rows),
            "note": ctx.tr(f"Групп: {len(groups)} · лишнее место: {human_size(wasted)}", f"Groups: {len(groups)} · wasted: {human_size(wasted, True)}")
            + (ctx.tr(" · неполный поиск (лимит)", " · partial (limit)") if partial else "")}

@utility("sys.empty_folders", "system", "Пустые папки", "Empty folders",
         "Находит пустые папки (и папки, где только пустые папки); по желанию удаляет их.",
         "Finds empty folders (and folders holding only empty folders); deletes them on request.",
         input="text", pick="folder", danger_if="delete", placeholder="D:\\Проекты", placeholder_en="D:\\Projects",
         opts=[O("delete", "bool", "Удалить найденные", "Delete them", False)], keys="пустые папки удалить empty folders directories cleanup")
def sys_empty_folders(ctx):
    root = _folder(ctx)
    dirs = [p for p, _st, _d in _walk(root, files=False, dirs=True)]
    dirs.sort(key=lambda p: -p.count(os.sep))
    empty = set()
    for d in dirs:
        try:
            with os.scandir(d) as it:
                if all(e.is_dir(follow_symlinks=False) and e.path in empty for e in it):
                    empty.add(d)
        except OSError:
            continue
    found = sorted(empty)
    removed = 0
    if ctx.o["delete"]:
        for d in sorted(found, key=lambda p: -p.count(os.sep)):
            try:
                os.rmdir(d)
                removed += 1
            except OSError:
                continue
    rows = [[os.path.relpath(d, root)] for d in found[:2000]]
    if not rows:
        return ctx.tr("Пустых папок нет", "No empty folders")
    return {"table": table([ctx.tr("Папка", "Folder")], rows),
            "note": ctx.tr(f"Найдено: {len(found)}" + (f" · удалено: {removed}" if ctx.o["delete"] else ""), f"Found: {len(found)}" + (f" · deleted: {removed}" if ctx.o["delete"] else ""))}

@utility("sys.rename", "system", "Массовое переименование", "Bulk rename",
         "Переименовывает файлы папки по шаблону ({name}, {n}, {ext}, {date}), заменой текста, транслитом или регистром. Сначала — предпросмотр.",
         "Renames a folder's files by a pattern ({name}, {n}, {ext}, {date}), text replacement, transliteration or case. A preview comes first.",
         input="text", pick="folder", danger_if="apply", placeholder="D:\\Фото\\Отпуск", placeholder_en="D:\\Photos\\Trip",
         opts=[O("pattern", "text", "Шаблон", "Pattern", "{name}", hint="{name} — имя, {n} — номер, {date} — дата файла, {ext} — расширение", hint_en="{name} = name, {n} = number, {date} = file date, {ext} = extension"),
               O("find", "text", "Найти", "Find", ""), O("replace", "text", "Заменить на", "Replace with", ""),
               O("case", "choice", "Регистр", "Case", "", choices=[("", "Как есть", "Keep"), ("lower", "строчные", "lower"), ("upper", "ПРОПИСНЫЕ", "UPPER"), ("title", "Каждое Слово", "Title")]),
               O("translit", "bool", "Транслит (кириллица → латиница)", "Transliterate (Cyrillic → Latin)", False), O("start", "int", "Нумерация с", "Numbers from", 1, min=0, max=1_000_000),
               O("digits", "int", "Цифр в номере", "Number digits", 3, min=1, max=8), O("only", "text", "Только расширения (jpg,png)", "Only extensions (jpg,png)", ""),
               O("apply", "bool", "Переименовать (иначе только предпросмотр)", "Rename (otherwise preview only)", False)],
         keys="переименовать файлы массово шаблон нумерация bulk rename batch files pattern")
def sys_rename(ctx):
    from .t_text import translit
    root = _folder(ctx)
    exts = {e.strip().lower().lstrip(".") for e in str(ctx.o["only"] or "").split(",") if e.strip()}
    files = sorted((p for p in root.iterdir() if p.is_file() and (not exts or p.suffix.lower().lstrip(".") in exts)), key=lambda p: p.name.lower())
    if not files:
        raise UtilError("В папке нет подходящих файлов", "No matching files in the folder")
    plan = []
    used = set()
    for i, p in enumerate(files):
        stem, ext = p.stem, p.suffix
        date = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
        name = str(ctx.o["pattern"] or "{name}").replace("{name}", stem).replace("{n}", str(ctx.o["start"] + i).zfill(ctx.o["digits"])).replace("{date}", date).replace("{ext}", ext.lstrip("."))
        if ctx.o["find"]:
            name = name.replace(ctx.o["find"], ctx.o["replace"] or "")
        if ctx.o["translit"]:
            name = translit(name)
        name = {"lower": name.lower(), "upper": name.upper(), "title": name.title()}.get(ctx.o["case"], name)
        name = re.sub(r"[\\/:*?\"<>|]+", "_", name).strip(" .") or f"file{i + 1}"
        target = name + ext
        if target.lower() in used:
            target = f"{name} ({i + 1}){ext}"
        used.add(target.lower())
        plan.append((p, target))
    changed = [(p, t) for p, t in plan if p.name != t]
    done = 0
    if ctx.o["apply"] and changed:
        temp = []
        for p, t in changed:
            tmp = p.with_name(f".memu-rename-{os.getpid()}-{done}-{p.name}")
            p.rename(tmp)
            temp.append((tmp, p.with_name(t), p))
            done += 1
        for tmp, final, original in temp:
            try:
                if final.exists():
                    final = final.with_name(f"{final.stem} (renamed){final.suffix}")
                tmp.rename(final)
            except OSError:
                tmp.rename(original)
    rows = [[p.name, "→", t] for p, t in changed[:1000]]
    if not rows:
        return ctx.tr("Имена не меняются — поправьте шаблон", "Nothing changes — adjust the pattern")
    return {"table": table([ctx.tr("Было", "Before"), "", ctx.tr("Станет", "After") if not ctx.o["apply"] else ctx.tr("Стало", "After")], rows),
            "note": ctx.tr(f"Переименовано: {done}", f"Renamed: {done}") if ctx.o["apply"] else ctx.tr(f"Предпросмотр: изменится {len(changed)} из {len(plan)} — включите «Переименовать»",
                                                                                                      f"Preview: {len(changed)} of {len(plan)} will change — turn on “Rename”")}

# ── processes and ports ──────────────────────────────────────────────────────────────────────────────────────────────
@utility("sys.processes", "system", "Процессы: кто ест память и процессор", "Processes: what eats RAM and CPU",
         "Топ процессов по памяти или процессору, с объединением одинаковых (все chrome.exe вместе).",
         "Top processes by memory or CPU, with identical ones grouped (all chrome.exe together).",
         input="none", ai_default=True,
         opts=[O("sort", "choice", "Сортировка", "Sort by", "mem", choices=[("mem", "Память", "Memory"), ("cpu", "Процессор", "CPU")]),
               O("group", "bool", "Объединять одинаковые", "Group identical", True), O("top", "int", "Сколько показать", "How many", 25, min=5, max=300)],
         keys="процессы диспетчер задач память процессор тормозит processes task manager memory cpu")
def sys_processes(ctx):
    psutil = _psutil()
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            p.cpu_percent(None)
            procs.append(p)
        except (psutil.Error, OSError):
            continue
    time.sleep(0.6)
    stats: Dict[Any, List[Any]] = {}
    cores = psutil.cpu_count() or 1
    for p in procs:
        try:
            cpu = p.cpu_percent(None) / cores
            mem = (p.info.get("memory_info").rss if p.info.get("memory_info") else 0)
            name = p.info.get("name") or str(p.pid)
        except (psutil.Error, OSError):
            continue
        key = name.lower() if ctx.o["group"] else p.pid
        row = stats.setdefault(key, [name, 0.0, 0, 0, p.pid])
        row[1] += cpu
        row[2] += mem
        row[3] += 1
    rows = sorted(stats.values(), key=lambda r: -(r[2] if ctx.o["sort"] == "mem" else r[1]))[:ctx.o["top"]]
    vm = psutil.virtual_memory()
    return {"table": table([ctx.tr("Процесс", "Process"), "PID" if not ctx.o["group"] else ctx.tr("Копий", "Count"), ctx.tr("Память", "Memory"), ctx.tr("ЦП", "CPU")],
                           [[r[0], str(r[4] if not ctx.o["group"] else r[3]), human_size(r[2], ctx.en), f"{r[1]:.1f}%"] for r in rows]),
            "note": ctx.tr(f"Память занята на {vm.percent:.0f}% из {human_size(vm.total)}", f"Memory {vm.percent:.0f}% used of {human_size(vm.total, True)}")}

@utility("sys.kill", "system", "Завершить процесс", "End a process",
         "Завершает зависшую программу по имени (chrome.exe) или PID — как «Снять задачу».",
         "Ends a hung program by name (chrome.exe) or PID — like “End task”.",
         input="text", danger=True, placeholder="notepad.exe или 1234", placeholder_en="notepad.exe or 1234",
         opts=[O("force", "bool", "Принудительно", "Force", False)], keys="завершить процесс убить снять задачу kill process end task")
def sys_kill(ctx):
    psutil = _psutil()
    target = ctx.need_text("Введите имя процесса или PID", "Enter a process name or PID").strip()
    me = os.getpid()
    victims = []
    for p in psutil.process_iter(["pid", "name"]):
        if p.pid in (me, os.getppid()) or p.pid <= 4:
            continue
        if (target.isdigit() and p.pid == int(target)) or (not target.isdigit() and (p.info.get("name") or "").lower() in (target.lower(), target.lower() + ".exe")):
            victims.append(p)
    if not victims:
        raise UtilError(f"Процесс «{target}» не найден", f"Process “{target}” not found")
    protected = {"csrss.exe", "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "smss.exe", "system", "svchost.exe", "dwm.exe", "init", "systemd"}
    done = 0
    for p in victims:
        if (p.info.get("name") or "").lower() in protected:
            raise UtilError("Это системный процесс — его завершать нельзя", "This is a system process — it must not be ended")
        try:
            p.kill() if ctx.o["force"] else p.terminate()
            done += 1
        except (psutil.Error, OSError):
            continue
    return ctx.tr(f"Завершено процессов: {done} из {len(victims)}", f"Processes ended: {done} of {len(victims)}")

@utility("sys.listening", "system", "Открытые порты этого ПК", "This PC's open ports",
         "Какие программы слушают сетевые порты на этом компьютере (сервера, игры, торренты) — с PID и именем.",
         "Which programs listen on network ports on this computer (servers, games, torrents) — with PID and name.",
         input="none", ai_default=True, keys="порты слушает netstat открытые порты программа listening ports netstat")
def sys_listening(ctx):
    psutil = _psutil()
    rows = []
    try:
        conns = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, OSError):
        raise UtilError("Нет прав на список соединений — запустите от администратора", "No permission to list connections — run as administrator")
    names: Dict[int, str] = {}
    for c in conns:
        if c.status != psutil.CONN_LISTEN and not (c.type == 2 and not c.raddr):
            continue
        pid = c.pid or 0
        if pid and pid not in names:
            try:
                names[pid] = psutil.Process(pid).name()
            except (psutil.Error, OSError):
                names[pid] = ""
        rows.append(["TCP" if c.type == 1 else "UDP", f"{c.laddr.ip}:{c.laddr.port}", str(pid or ""), names.get(pid, "")])
    rows = sorted({tuple(r) for r in rows}, key=lambda r: (r[0], int(r[1].rsplit(":", 1)[1])))
    return {"table": table([ctx.tr("Протокол", "Protocol"), ctx.tr("Адрес", "Address"), "PID", ctx.tr("Программа", "Program")], [list(r) for r in rows[:1000]])}

# ── Windows: startup, installed programs ─────────────────────────────────────────────────────────────────────────────
def _reg_values(hive, path: str) -> List[Tuple[str, Any]]:
    import winreg
    out = []
    try:
        with winreg.OpenKey(hive, path) as key:
            i = 0
            while True:
                try:
                    name, value, _t = winreg.EnumValue(key, i)
                except OSError:
                    break
                out.append((name, value))
                i += 1
    except OSError:
        pass
    return out

@utility("sys.startup", "system", "Автозагрузка", "Startup programs",
         "Что запускается вместе с Windows: реестр (Run) и папки автозагрузки, включено или отключено в Диспетчере задач.",
         "What starts with Windows: the registry (Run) and Startup folders, enabled or disabled in Task Manager.",
         input="none", platform="win", ai_default=True, keys="автозагрузка автозапуск тормозит загрузка startup autorun boot")
def sys_startup(ctx):
    _need_win(ctx)
    import winreg
    rows = []
    approved: Dict[str, bool] = {}
    for hive, base in ((winreg.HKEY_CURRENT_USER, "HKCU"), (winreg.HKEY_LOCAL_MACHINE, "HKLM")):
        for sub in ("Run", "Run32", "StartupFolder"):
            for name, value in _reg_values(hive, rf"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\{sub}"):
                if isinstance(value, bytes) and value:
                    approved[name.lower()] = value[0] in (0x02, 0x06)
    for hive, label in ((winreg.HKEY_CURRENT_USER, ctx.tr("Пользователь", "User")), (winreg.HKEY_LOCAL_MACHINE, ctx.tr("Все пользователи", "All users"))):
        for path in (r"Software\Microsoft\Windows\CurrentVersion\Run", r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run", r"Software\Microsoft\Windows\CurrentVersion\RunOnce"):
            for name, value in _reg_values(hive, path):
                state = approved.get(name.lower(), True)
                rows.append([name, ctx.tr("включено", "enabled") if state else ctx.tr("отключено", "disabled"), label + (" · RunOnce" if "RunOnce" in path else ""), str(value)[:200]])
    for folder, label in ((Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Startup", ctx.tr("Папка пользователя", "User folder")),
                          (Path(os.environ.get("PROGRAMDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\StartUp", ctx.tr("Общая папка", "Common folder"))):
        try:
            for item in folder.iterdir():
                if item.name.lower() != "desktop.ini":
                    state = approved.get(item.name.lower(), True)
                    rows.append([item.stem, ctx.tr("включено", "enabled") if state else ctx.tr("отключено", "disabled"), label, str(item)])
        except OSError:
            continue
    return {"table": table([ctx.tr("Программа", "Program"), ctx.tr("Состояние", "State"), ctx.tr("Где", "Where"), ctx.tr("Команда", "Command")], rows),
            "note": ctx.tr("Отключать — в Диспетчере задач → Автозагрузка", "To disable: Task Manager → Startup apps")}

@utility("sys.installed", "system", "Установленные программы", "Installed programs",
         "Список установленных программ с версией, издателем, датой установки и размером; поиск по названию.",
         "The installed programs with version, publisher, install date and size; search by name.",
         input="text", platform="win", ai_default=True, placeholder="поиск (необязательно)", placeholder_en="search (optional)",
         opts=[O("sort", "choice", "Сортировка", "Sort by", "name", choices=[("name", "Название", "Name"), ("size", "Размер", "Size"), ("date", "Дата установки", "Install date")])],
         keys="установленные программы приложения список удалить installed programs apps software list")
def sys_installed(ctx):
    _need_win(ctx)
    import winreg
    items: Dict[str, List[Any]] = {}
    roots = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall")]
    for hive, path in roots:
        try:
            with winreg.OpenKey(hive, path) as key:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(key, i)
                    except OSError:
                        break
                    i += 1
                    values = dict(_reg_values(hive, f"{path}\\{sub}"))
                    name = str(values.get("DisplayName") or "").strip()
                    if not name or values.get("SystemComponent") == 1 or values.get("ParentKeyName"):
                        continue
                    date = str(values.get("InstallDate") or "")
                    if re.fullmatch(r"\d{8}", date):
                        date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
                    size = int(values.get("EstimatedSize") or 0) * 1024
                    items.setdefault(name.lower(), [name, str(values.get("DisplayVersion") or ""), str(values.get("Publisher") or ""), date, size])
        except OSError:
            continue
    query = str(ctx.text or "").strip().lower()
    rows = [r for r in items.values() if not query or query in r[0].lower() or query in r[2].lower()]
    key = {"name": lambda r: r[0].lower(), "size": lambda r: -r[4], "date": lambda r: r[3] or "0"}[ctx.o["sort"]]
    rows.sort(key=key, reverse=ctx.o["sort"] == "date")
    return {"table": table([ctx.tr("Программа", "Program"), ctx.tr("Версия", "Version"), ctx.tr("Издатель", "Publisher"), ctx.tr("Установлена", "Installed"), ctx.tr("Размер", "Size")],
                           [[r[0], r[1], r[2], r[3], human_size(r[4], ctx.en) if r[4] else ""] for r in rows]),
            "note": ctx.tr(f"Программ: {len(rows)}", f"Programs: {len(rows)}")}

# ── cleanup ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def _temp_dirs() -> List[Tuple[str, Path]]:
    dirs = [("Temp", Path(tempfile.gettempdir()))]
    if WIN:
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        dirs += [("Windows Temp", Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Temp"), ("CrashDumps", local / "CrashDumps"),
                 ("DirectX Shader Cache", local / "D3DSCache"), ("Windows Error Reports", local / r"Microsoft\Windows\WER\ReportArchive"),
                 ("NVIDIA Shader Cache", local / r"NVIDIA\DXCache"), ("AMD Shader Cache", local / r"AMD\DxCache")]
    else:
        dirs.append(("Cache thumbnails", Path.home() / ".cache" / "thumbnails"))
    seen, out = set(), []
    for label, path in dirs:
        try:
            real = path.resolve()
        except OSError:
            continue
        if real.is_dir() and str(real).lower() not in seen:
            seen.add(str(real).lower())
            out.append((label, real))
    return out

@utility("sys.tempclean", "system", "Очистка временных файлов", "Temp files cleanup",
         "Находит мусор: временные файлы, дампы падений, кэши шейдеров, отчёты об ошибках, корзину — сначала только подсчёт.",
         "Finds junk: temp files, crash dumps, shader caches, error reports, the Recycle Bin — counting only at first.",
         input="none", danger_if="mode=delete",
         opts=[O("mode", "choice", "Режим", "Mode", "scan", choices=[("scan", "Только посчитать", "Count only"), ("delete", "Удалить", "Delete")]),
               O("days", "int", "Старше, дней", "Older than, days", 2, min=0, max=3650), O("bin", "bool", "Очистить корзину", "Empty the Recycle Bin", False)],
         keys="очистка мусор временные файлы temp кэш корзина освободить место cleanup junk cache recycle bin")
def sys_tempclean(ctx):
    cutoff = time.time() - ctx.o["days"] * 86400
    delete = ctx.o["mode"] == "delete"
    rows = []
    total_size = total_files = freed = 0
    own = Path(tempfile.gettempdir()).resolve()
    for label, folder in _temp_dirs():
        size = files = removed = 0
        try:
            for path, st, _d in _walk(folder):
                if st.st_mtime > cutoff:
                    continue
                if "memuai" in path.lower() and folder == own:
                    continue
                size += st.st_size
                files += 1
                if delete:
                    try:
                        os.remove(path)
                        removed += st.st_size
                    except OSError:
                        pass
        except _WalkLimit:
            pass
        if delete:
            for path, _st, _d in sorted(_walk(folder, files=False, dirs=True), key=lambda x: -x[0].count(os.sep)):
                try:
                    os.rmdir(path)
                except OSError:
                    pass
        total_size += size
        total_files += files
        freed += removed
        rows.append([label, f"{files:,}".replace(",", " "), human_size(size, ctx.en), human_size(removed, ctx.en) if delete else "", str(folder)])
    if ctx.o["bin"] and WIN:
        try:
            import ctypes
            if delete:
                ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x07)
            rows.append([ctx.tr("Корзина", "Recycle Bin"), "", "", ctx.tr("очищена", "emptied") if delete else ctx.tr("будет очищена", "will be emptied"), ""])
        except Exception:  # noqa: BLE001
            pass
    note = ctx.tr(f"Освобождено {human_size(freed)} (занятые программами файлы пропущены)", f"Freed {human_size(freed, True)} (files in use were skipped)") if delete else \
        ctx.tr(f"Можно освободить около {human_size(total_size)} — выберите «Удалить»", f"About {human_size(total_size, True)} can be freed — choose “Delete”")
    return {"table": table([ctx.tr("Что", "What"), ctx.tr("Файлов", "Files"), ctx.tr("Размер", "Size"), ctx.tr("Удалено", "Deleted"), ctx.tr("Папка", "Folder")], rows), "note": note}

# ── Windows tweaks (HKCU, reversible) ────────────────────────────────────────────────────────────────────────────────
ADV = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
TWEAKS: List[Tuple[str, str, str, List[Tuple], List[Tuple], bool]] = [
    # key, ru, en, apply ops, revert ops, needs Explorer restart; op = (action, path, name, type, value)
    ("ext", "Показывать расширения файлов", "Show file extensions", [("set", ADV, "HideFileExt", "dword", 0)], [("set", ADV, "HideFileExt", "dword", 1)], True),
    ("hidden", "Показывать скрытые файлы", "Show hidden files", [("set", ADV, "Hidden", "dword", 1)], [("set", ADV, "Hidden", "dword", 2)], True),
    ("thispc", "Проводник открывает «Этот компьютер»", "Explorer opens “This PC”", [("set", ADV, "LaunchTo", "dword", 1)], [("set", ADV, "LaunchTo", "dword", 2)], False),
    ("classic_menu", "Классическое контекстное меню (Windows 11)", "Classic context menu (Windows 11)",
     [("set", r"Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}\InprocServer32", "", "sz", "")],
     [("delkey", r"Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}\InprocServer32", "", "", None),
      ("delkey", r"Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}", "", "", None)], True),
    ("taskbar_left", "Панель задач слева (Windows 11)", "Taskbar aligned left (Windows 11)", [("set", ADV, "TaskbarAl", "dword", 0)], [("set", ADV, "TaskbarAl", "dword", 1)], False),
    ("seconds", "Секунды в часах на панели задач", "Seconds in the taskbar clock", [("set", ADV, "ShowSecondsInSystemClock", "dword", 1)], [("set", ADV, "ShowSecondsInSystemClock", "dword", 0)], True),
    ("end_task", "«Завершить задачу» в меню панели задач", "“End task” in the taskbar menu",
     [("set", ADV + r"\TaskbarDeveloperSettings", "TaskbarEndTask", "dword", 1)], [("set", ADV + r"\TaskbarDeveloperSettings", "TaskbarEndTask", "dword", 0)], False),
    ("no_bing", "Без веб-поиска Bing в меню «Пуск»", "No Bing web search in Start",
     [("set", r"Software\Policies\Microsoft\Windows\Explorer", "DisableSearchBoxSuggestions", "dword", 1), ("set", r"Software\Microsoft\Windows\CurrentVersion\Search", "BingSearchEnabled", "dword", 0)],
     [("del", r"Software\Policies\Microsoft\Windows\Explorer", "DisableSearchBoxSuggestions", "", None), ("del", r"Software\Microsoft\Windows\CurrentVersion\Search", "BingSearchEnabled", "", None)], True),
    ("dark", "Тёмная тема Windows и приложений", "Dark mode for Windows and apps",
     [("set", r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "AppsUseLightTheme", "dword", 0), ("set", r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "SystemUsesLightTheme", "dword", 0)],
     [("set", r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "AppsUseLightTheme", "dword", 1), ("set", r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "SystemUsesLightTheme", "dword", 1)], False),
    ("compact", "Компактный вид Проводника", "Compact Explorer view", [("set", ADV, "UseCompactMode", "dword", 1)], [("set", ADV, "UseCompactMode", "dword", 0)], True),
    ("mouse_accel", "Отключить ускорение мыши", "Turn off mouse acceleration",
     [("set", r"Control Panel\Mouse", "MouseSpeed", "sz", "0"), ("set", r"Control Panel\Mouse", "MouseThreshold1", "sz", "0"), ("set", r"Control Panel\Mouse", "MouseThreshold2", "sz", "0")],
     [("set", r"Control Panel\Mouse", "MouseSpeed", "sz", "1"), ("set", r"Control Panel\Mouse", "MouseThreshold1", "sz", "6"), ("set", r"Control Panel\Mouse", "MouseThreshold2", "sz", "10")], False),
    ("sticky", "Не спрашивать про залипание клавиш (5×Shift)", "No Sticky Keys prompt (5×Shift)",
     [("set", r"Control Panel\Accessibility\StickyKeys", "Flags", "sz", "506")], [("set", r"Control Panel\Accessibility\StickyKeys", "Flags", "sz", "510")], False),
]

def _tweak_state(tw) -> Optional[bool]:
    import winreg
    action, path, name, _kind, value = tw[3][0]
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            current = winreg.QueryValueEx(key, name)[0]
        return str(current) == str(value)
    except OSError:
        return False if action == "set" else None

def _tweak_apply(ops) -> None:
    import winreg
    for action, path, name, kind, value in ops:
        if action == "set":
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, name, 0, winreg.REG_DWORD if kind == "dword" else winreg.REG_SZ, value)
        elif action == "del":
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, name)
            except OSError:
                pass
        elif action == "delkey":
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
            except OSError:
                pass

@utility("sys.tweaks", "system", "Твики Windows", "Windows tweaks",
         "Полезные настройки текущего пользователя одним щелчком: расширения файлов, классическое меню Windows 11, без Bing в «Пуске», секунды в часах… Всё обратимо.",
         "Handy current-user settings in one click: file extensions, the classic Windows 11 menu, no Bing in Start, seconds in the clock… All reversible.",
         input="none", platform="win", danger_if="action=apply|revert",
         opts=[O("action", "choice", "Действие", "Action", "status", choices=[("status", "Показать состояние", "Show status"), ("apply", "Включить выбранные", "Turn on selected"), ("revert", "Вернуть как было", "Revert selected")])]
         + [O(t[0], "bool", t[1], t[2], False, show="action=apply|revert") for t in TWEAKS]
         + [O("restart", "bool", "Перезапустить Проводник", "Restart Explorer", True, show="action=apply|revert")],
         keys="твики windows настройки реестр расширения файлов контекстное меню bing tweaks registry explorer")
def sys_tweaks(ctx):
    _need_win(ctx)
    action = ctx.o["action"]
    if action == "status":
        rows = [[t[2] if ctx.en else t[1], {True: ctx.tr("включено ✓", "on ✓"), False: ctx.tr("выключено", "off"), None: "—"}[_tweak_state(t)]] for t in TWEAKS]
        return {"table": table([ctx.tr("Твик", "Tweak"), ctx.tr("Сейчас", "Now")], rows)}
    chosen = [t for t in TWEAKS if ctx.o.get(t[0])]
    if not chosen:
        raise UtilError("Отметьте хотя бы один твик", "Tick at least one tweak")
    restart = False
    for t in chosen:
        _tweak_apply(t[3] if action == "apply" else t[4])
        restart = restart or t[5]
    try:
        import ctypes
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "ImmersiveColorSet", 0x0002, 2000, None)
    except Exception:  # noqa: BLE001
        pass
    if restart and ctx.o["restart"]:
        _restart_explorer()
    names = ", ".join(t[2] if ctx.en else t[1] for t in chosen)
    return {"text": (ctx.tr("Включено: ", "Turned on: ") if action == "apply" else ctx.tr("Возвращено: ", "Reverted: ")) + names,
            "note": ctx.tr("Часть изменений видна после перезапуска Проводника или входа в систему", "Some changes show after restarting Explorer or signing in again") if restart and not ctx.o["restart"] else ""}

def _restart_explorer() -> None:
    subprocess.run(["taskkill", "/f", "/im", "explorer.exe"], capture_output=True, creationflags=NO_WINDOW, timeout=20)
    time.sleep(1.0)
    subprocess.Popen(["explorer.exe"], creationflags=NO_WINDOW, close_fds=True)

@utility("sys.explorer", "system", "Перезапустить Проводник", "Restart Explorer",
         "Перезапускает Проводник Windows — помогает, когда зависли панель задач, «Пуск» или значки.",
         "Restarts Windows Explorer — helps when the taskbar, Start or icons freeze.",
         input="none", platform="win", danger=True, keys="перезапустить проводник explorer панель задач зависла restart explorer taskbar")
def sys_explorer(ctx):
    _need_win(ctx)
    _restart_explorer()
    return ctx.tr("Проводник перезапущен", "Explorer restarted")

@utility("sys.power", "system", "Таймер выключения", "Shutdown timer",
         "Выключит, перезагрузит или переведёт компьютер в сон/гибернацию через заданное время; можно отменить запланированное.",
         "Shuts down, restarts, sleeps or hibernates the computer after a delay; a scheduled shutdown can be cancelled.",
         input="none", danger=True,
         opts=[O("action", "choice", "Действие", "Action", "shutdown", choices=[("shutdown", "Выключить", "Shut down"), ("restart", "Перезагрузить", "Restart"), ("sleep", "Сон", "Sleep"),
                                                                                ("hibernate", "Гибернация", "Hibernate"), ("cancel", "Отменить запланированное", "Cancel the scheduled one")]),
               O("minutes", "int", "Через, минут", "In, minutes", 60, min=0, max=10080, show="action=shutdown|restart|sleep|hibernate")],
         keys="таймер выключения выключить компьютер перезагрузка сон shutdown timer restart sleep hibernate")
def sys_power(ctx):
    action, minutes = ctx.o["action"], ctx.o["minutes"]
    if action == "cancel":
        stopped = jobs.stop(kind="power")
        try:
            code, out = _run(["shutdown", "/a"] if WIN else ["shutdown", "-c"])
        except UtilError:
            code, out = 1, ""
        if code == 0 or stopped:
            return ctx.tr("Запланированное действие отменено", "The scheduled action was cancelled")
        return ctx.tr("Нечего отменять", "Nothing to cancel")
    if WIN:
        if action in ("shutdown", "restart"):
            code, out = _run(["shutdown", "/s" if action == "shutdown" else "/r", "/t", str(minutes * 60)])
            if code:
                raise UtilError(out.strip() or "shutdown failed", out.strip() or "shutdown failed")
        else:
            def later(job):
                if not job.sleep(minutes * 60):
                    if action == "hibernate":
                        subprocess.run(["shutdown", "/h"], creationflags=NO_WINDOW)
                    else:
                        subprocess.run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], creationflags=NO_WINDOW)
            jobs.start("power", "Сон по таймеру", "Sleep timer", later)
    else:
        if action in ("shutdown", "restart"):
            code, out = _run(["shutdown", "-h" if action == "shutdown" else "-r", f"+{max(0, minutes)}"])
            if code:
                raise UtilError(out.strip() or "shutdown failed", out.strip() or "shutdown failed")
        else:
            def later(job):
                if not job.sleep(minutes * 60):
                    subprocess.run(["systemctl", "suspend" if action == "sleep" else "hibernate"])
            jobs.start("power", "Сон по таймеру", "Sleep timer", later)
    when = datetime.fromtimestamp(time.time() + minutes * 60).strftime("%H:%M")
    names = {"shutdown": ("Выключение", "Shutdown"), "restart": ("Перезагрузка", "Restart"), "sleep": ("Сон", "Sleep"), "hibernate": ("Гибернация", "Hibernation")}[action]
    return ctx.tr(f"{names[0]} в {when} (через {minutes} мин). Отменить — действие «Отменить запланированное»",
                  f"{names[1]} at {when} (in {minutes} min). To cancel, run “Cancel the scheduled one”")

@utility("sys.flushdns", "system", "Сбросить кэш DNS", "Flush the DNS cache",
         "Очищает кэш DNS системы — помогает, когда сайт переехал или «не открывается только у меня».",
         "Clears the system DNS cache — helps when a site has moved or “does not open only for me”.",
         input="none", keys="сбросить dns кэш flushdns ipconfig сайт не открывается flush dns cache")
def sys_flushdns(ctx):
    if WIN:
        code, out = _run(["ipconfig", "/flushdns"])
    else:
        try:
            code, out = _run(["resolvectl", "flush-caches"])
        except UtilError:
            try:
                code, out = _run(["systemd-resolve", "--flush-caches"])
            except UtilError:
                raise UtilError("В этой системе нет systemd-resolved — кэш DNS не ведётся или очищается перезапуском сетевой службы",
                                "This system has no systemd-resolved — there is no DNS cache, or restarting the network service clears it")
    if code:
        raise UtilError(out.strip() or "flush failed", out.strip() or "flush failed")
    return out.strip() or ctx.tr("Кэш DNS очищен", "DNS cache flushed")

@utility("sys.hosts", "system", "Файл hosts", "The hosts file",
         "Показывает записи файла hosts (что перенаправлено или заблокировано) и где он лежит. Только чтение.",
         "Shows the hosts file entries (what is redirected or blocked) and where it lives. Read-only.",
         input="none", ai_default=True, keys="hosts файл блокировка сайтов перенаправление hosts file")
def sys_hosts(ctx):
    path = Path(os.environ.get("SystemRoot", r"C:\Windows")) / r"System32\drivers\etc\hosts" if WIN else Path("/etc/hosts")
    try:
        text = path.read_text("utf-8", "replace")
    except OSError as exc:
        raise UtilError(f"Не удалось прочитать {path}: {exc}", f"Could not read {path}: {exc}")
    rows = []
    for line in text.splitlines():
        body = line.split("#", 1)[0].strip()
        if body:
            parts = body.split()
            rows += [[parts[0], name] for name in parts[1:]]
    return {"table": table(["IP", ctx.tr("Имя", "Name")], rows) if rows else table(["IP", ctx.tr("Имя", "Name")], []),
            "note": f"{path} · " + ctx.tr(f"записей: {len(rows)}", f"entries: {len(rows)}")}

SECRET_NAME = re.compile(r"key|token|secret|passw|pwd|auth|cred|cookie|session", re.I)

@utility("sys.env", "system", "Переменные окружения", "Environment variables",
         "Все переменные окружения, PATH по строкам; значения, похожие на ключи и пароли, скрыты.",
         "All environment variables with PATH split into lines; values that look like keys and passwords are hidden.",
         input="text", ai=False, placeholder="фильтр (необязательно)", placeholder_en="filter (optional)",
         keys="переменные окружения path env environment variables")
def sys_env(ctx):
    query = str(ctx.text or "").strip().lower()
    rows = []
    for name, value in sorted(os.environ.items(), key=lambda kv: kv[0].lower()):
        if query and query not in name.lower() and query not in value.lower():
            continue
        if SECRET_NAME.search(name):
            value = (value[:3] + "…" + ctx.tr(" (скрыто)", " (hidden)")) if value else ""
        if name.upper() == "PATH":
            for k, part in enumerate(value.split(os.pathsep)):
                if part:
                    rows.append([name if k == 0 else "", part + ("" if os.path.isdir(part) else ctx.tr("  ✗ нет папки", "  ✗ missing"))])
        else:
            rows.append([name, value[:500]])
    return {"table": table([ctx.tr("Переменная", "Variable"), ctx.tr("Значение", "Value")], rows)}

@utility("sys.battery", "system", "Батарея ноутбука", "Laptop battery",
         "Заряд, время работы, подключено ли питание; в Windows — подробный отчёт об износе батареи (powercfg).",
         "Charge, time left, power plugged in; on Windows a detailed battery wear report (powercfg).",
         input="none", ai_default=True, opts=[O("report", "bool", "Подробный отчёт (Windows)", "Detailed report (Windows)", True)],
         keys="батарея аккумулятор износ заряд ноутбук battery health wear report")
def sys_battery(ctx):
    psutil = _psutil()
    try:
        bat = psutil.sensors_battery()
    except Exception:  # noqa: BLE001
        bat = None
    info = []
    if bat:
        left = ""
        if bat.secsleft not in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN) and bat.secsleft > 0:
            left = ctx.tr(f"{bat.secsleft // 3600} ч {bat.secsleft % 3600 // 60} мин", f"{bat.secsleft // 3600} h {bat.secsleft % 3600 // 60} min")
        info = [[ctx.tr("Заряд", "Charge"), f"{bat.percent:.0f}%"], [ctx.tr("Питание", "Power"), ctx.tr("от сети", "plugged in") if bat.power_plugged else ctx.tr("от батареи", "on battery")]]
        if left:
            info.append([ctx.tr("Осталось", "Time left"), left])
    note = ""
    if WIN and ctx.o["report"]:
        target = ctx.out_path("battery-report.html")
        code, out = _run(["powercfg", "/batteryreport", "/output", str(target)], 60)
        if code or not target.exists():
            ctx.outputs.remove(target)
            note = out.strip()[:200]
        else:
            html = target.read_text("utf-8", "replace")
            design = re.search(r"DESIGN CAPACITY.*?<td>([\d\s,.]+)mWh", html, re.S | re.I)
            full = re.search(r"FULL CHARGE CAPACITY.*?<td>([\d\s,.]+)mWh", html, re.S | re.I)
            if design and full:
                d = int(re.sub(r"\D", "", design.group(1)) or 0)
                f = int(re.sub(r"\D", "", full.group(1)) or 0)
                if d:
                    info.append([ctx.tr("Ёмкость сейчас / новая", "Capacity now / design"), f"{f:,} / {d:,} mWh".replace(",", " ")])
                    info.append([ctx.tr("Износ", "Wear"), f"{max(0, 100 - f * 100 // d)}%"])
    if not info:
        return {"text": ctx.tr("Батарея не найдена — это настольный ПК?", "No battery found — a desktop PC?"), "note": note}
    return {"info": info, "note": note}

@utility("sys.wifi", "system", "Сохранённые пароли Wi-Fi", "Saved Wi-Fi passwords",
         "Показывает сети Wi-Fi, к которым подключался этот компьютер, и их пароли — чтобы подключить телефон или гостя.",
         "Shows the Wi-Fi networks this computer has joined and their passwords — to connect a phone or a guest.",
         input="none", ai=False, keys="пароль wifi вайфай сохранённые сети ключ wifi password saved networks key")
def sys_wifi(ctx):
    rows = []
    if WIN:
        _code, out = _run(["netsh", "wlan", "show", "profiles"])
        names = [m.strip() for m in re.findall(r"(?:All User Profile|Все профили пользователей|Профиль всех пользователей)\s*:\s*(.+)", out)]
        for name in names[:200]:
            _c, detail = _run(["netsh", "wlan", "show", "profile", f"name={name}", "key=clear"])
            key = re.search(r"(?:Key Content|Содержимое ключа)\s*:\s*(.+)", detail)
            auth = re.search(r"(?:Authentication|Проверка подлинности)\s*:\s*(.+)", detail)
            rows.append([name, key.group(1).strip() if key else ctx.tr("(нет / открытая)", "(none / open)"), auth.group(1).strip() if auth else ""])
    else:
        code, out = _run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"])
        for line in out.splitlines():
            name, _, kind = line.rpartition(":")
            if "wireless" in kind:
                _c, psk = _run(["nmcli", "-s", "-g", "802-11-wireless-security.psk", "connection", "show", name])
                rows.append([name.replace("\\:", ":"), psk.strip() or ctx.tr("(нет доступа или открытая)", "(no access or open)"), ""])
    if not rows:
        return ctx.tr("Сохранённых сетей Wi-Fi нет", "No saved Wi-Fi networks")
    return {"table": table([ctx.tr("Сеть", "Network"), ctx.tr("Пароль", "Password"), ctx.tr("Защита", "Security")], rows)}

# ── input automation and keep-awake (background jobs, window only) ──────────────────────────────────────────────────
def _win_input():
    import ctypes
    from ctypes import wintypes

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    return ctypes, wintypes, user32, INPUT, MOUSEINPUT, KEYBDINPUT

BUTTONS = {"left": (0x0002, 0x0004, 1), "right": (0x0008, 0x0010, 3), "middle": (0x0020, 0x0040, 2)}

@utility("sys.clicker", "system", "Автокликер", "Auto-clicker",
         "Щёлкает мышью с заданным интервалом в текущей точке или по координатам. Остановка — кнопкой «Стоп», клавишей Esc или курсором в левый верхний угол.",
         "Clicks the mouse at a set interval at the cursor or at fixed coordinates. Stop with the Stop button, Esc or the cursor in the top-left corner.",
         input="none", ai=False,
         opts=[O("interval", "int", "Интервал, мс", "Interval, ms", 100, min=10, max=3_600_000), O("button", "choice", "Кнопка", "Button", "left",
                                                                                                    choices=[("left", "Левая", "Left"), ("right", "Правая", "Right"), ("middle", "Средняя", "Middle")]),
               O("double", "bool", "Двойной щелчок", "Double click", False), O("count", "int", "Сколько щелчков (0 — пока не остановлю)", "Clicks (0 = until stopped)", 0, min=0, max=10_000_000),
               O("delay", "int", "Старт через, с", "Start in, s", 3, min=0, max=600), O("at", "text", "Координаты x,y (пусто — где курсор)", "Coordinates x,y (empty = cursor)", ""),
               O("jitter", "int", "Случайный разброс интервала, %", "Random interval jitter, %", 0, min=0, max=90)],
         keys="автокликер кликер автоклик мышь щелчок auto clicker autoclicker mouse click")
def sys_clicker(ctx):
    import random
    o = dict(ctx.o)
    at = None
    if str(o["at"] or "").strip():
        m = re.fullmatch(r"\s*(-?\d+)\s*[,; x]\s*(-?\d+)\s*", str(o["at"]))
        if not m:
            raise UtilError("Координаты — два числа: 640,360", "Coordinates are two numbers: 640,360")
        at = (int(m.group(1)), int(m.group(2)))
    if not WIN and not shutil.which("xdotool"):
        raise UtilError("Нужен Windows или установленный xdotool (Linux)", "Needs Windows or xdotool installed (Linux)")

    def run(job):
        if job.sleep(o["delay"]):
            return
        if WIN:
            ctypes, wintypes, user32, INPUT, MOUSEINPUT, _K = _win_input()
            down, up, _x = BUTTONS[o["button"]]
            seq = [down, up] * (2 if o["double"] else 1)
            arr = (INPUT * len(seq))()
            for i, flag in enumerate(seq):
                arr[i].type = 0
                arr[i].mi = MOUSEINPUT(0, 0, 0, flag, 0, 0)
            point = wintypes.POINT()
        while not job.stop_event.is_set():
            if WIN:
                user32.GetCursorPos(ctypes.byref(point))
                if (point.x <= 2 and point.y <= 2) or (user32.GetAsyncKeyState(0x1B) & 0x8000):
                    job.message, job.message_en = "Остановлено: Esc или угол экрана", "Stopped: Esc or the screen corner"
                    job.status = "stopped"
                    return
                if at:
                    user32.SetCursorPos(at[0], at[1])
                user32.SendInput(len(seq), arr, ctypes.sizeof(INPUT))
            else:
                cmd = ["xdotool"] + (["mousemove", str(at[0]), str(at[1])] if at else []) + ["click"] + (["--repeat", "2"] if o["double"] else []) + [str(BUTTONS[o["button"]][2])]
                # F35: a hung xdotool used to stall the clicker job forever.
                subprocess.run(cmd, capture_output=True, timeout=10)
            job.count += 1
            if o["count"] and job.count >= o["count"]:
                return
            wait = o["interval"] / 1000
            if o["jitter"]:
                wait *= 1 + random.uniform(-o["jitter"], o["jitter"]) / 100
            if job.sleep(wait):
                return
    job = jobs.start("clicker", "Автокликер", "Auto-clicker", run)
    return {"text": ctx.tr(f"Автокликер запустится через {o['delay']} с. Стоп — кнопкой, Esc или курсором в левый верхний угол.",
                           f"The auto-clicker starts in {o['delay']} s. Stop it with the button, Esc or the cursor in the top-left corner."),
            "job": job.public(ctx.en)}

@utility("sys.autotype", "system", "Автонабор текста", "Auto-type text",
         "Через несколько секунд «печатает» текст в активное окно — туда, где не работает вставка (консоли, удалённые рабочие столы, формы).",
         "After a few seconds “types” the text into the active window — where paste does not work (consoles, remote desktops, forms).",
         input="text", ai=False, placeholder="Текст для набора", placeholder_en="Text to type",
         opts=[O("delay", "int", "Старт через, с", "Start in, s", 3, min=0, max=120), O("speed", "int", "Пауза между символами, мс", "Pause between characters, ms", 15, min=0, max=2000)],
         keys="автонабор напечатать текст ввод клавиатура auto type typing keyboard macro")
def sys_autotype(ctx):
    text = ctx.need_text("Введите текст для набора", "Enter the text to type")
    delay, speed = ctx.o["delay"], ctx.o["speed"]
    if not WIN and not shutil.which("xdotool"):
        raise UtilError("Нужен Windows или установленный xdotool (Linux)", "Needs Windows or xdotool installed (Linux)")

    def run(job):
        if job.sleep(delay):
            return
        if not WIN:
            # F35: cap the wait (typing time scales with the text length).
            subprocess.run(["xdotool", "type", "--delay", str(max(1, speed)), text], capture_output=True,
                           timeout=max(60.0, len(text) * max(1, speed) / 1000.0 + 30.0))
            job.count = len(text)
            return
        ctypes, wintypes, user32, INPUT, _M, KEYBDINPUT = _win_input()

        def send(vk: int = 0, scan: int = 0, flags: int = 0):
            arr = (INPUT * 2)()
            for i, extra in enumerate((0, 0x0002)):
                arr[i].type = 1
                arr[i].ki = KEYBDINPUT(vk, scan, flags | extra, 0, 0)
            user32.SendInput(2, arr, ctypes.sizeof(INPUT))
        for ch in text.replace("\r\n", "\n"):
            if job.stop_event.is_set() or user32.GetAsyncKeyState(0x1B) & 0x8000:
                job.status = "stopped"
                return
            if ch == "\n":
                send(vk=0x0D)
            elif ch == "\t":
                send(vk=0x09)
            else:
                units = ch.encode("utf-16-le")
                for k in range(0, len(units), 2):
                    send(scan=int.from_bytes(units[k:k + 2], "little"), flags=0x0004)
            job.count += 1
            if speed and job.sleep(speed / 1000):
                return
    job = jobs.start("autotype", "Автонабор", "Auto-type", run)
    return {"text": ctx.tr(f"Переключитесь в нужное окно: набор начнётся через {delay} с ({len(text)} символов). Esc — стоп.",
                           f"Switch to the target window: typing starts in {delay} s ({len(text)} characters). Esc stops it."), "job": job.public(ctx.en)}

@utility("sys.keepawake", "system", "Не засыпать", "Keep awake",
         "Не даёт компьютеру уснуть и (по желанию) погасить экран — на время загрузки, рендера или презентации.",
         "Keeps the computer from sleeping and (optionally) the screen from turning off — during a download, a render or a talk.",
         input="none", ai=False,
         opts=[O("minutes", "int", "На сколько минут (0 — пока не остановлю)", "For how many minutes (0 = until stopped)", 60, min=0, max=100_000),
               O("display", "bool", "Не гасить экран", "Keep the screen on", True)],
         keys="не засыпать не спать сон экран кофеин caffeine keep awake no sleep stay awake")
def sys_keepawake(ctx):
    minutes, display = ctx.o["minutes"], ctx.o["display"]

    def run(job):
        deadline = time.time() + minutes * 60 if minutes else None
        proc = None
        if WIN:
            import ctypes
            flags = 0x80000000 | 0x00000001 | (0x00000002 if display else 0)
            ctypes.windll.kernel32.SetThreadExecutionState(flags)
        elif shutil.which("systemd-inhibit"):
            proc = subprocess.Popen(["systemd-inhibit", "--what=idle:sleep", "--why=MemuUtils keep awake", "sleep", str(minutes * 60 if minutes else 10 ** 7)])
        try:
            while not job.stop_event.is_set():
                if deadline and time.time() >= deadline:
                    return
                job.count = int(time.time() - job.started) // 60
                if job.sleep(5):
                    return
        finally:
            if WIN:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            if proc is not None:
                proc.terminate()
    job = jobs.start("keepawake", "Не засыпать", "Keep awake", run)
    until = datetime.fromtimestamp(time.time() + minutes * 60).strftime("%H:%M") if minutes else ""
    return {"text": ctx.tr(f"Компьютер не уснёт до {until}" if minutes else "Компьютер не уснёт, пока не нажмёте «Стоп»",
                           f"The computer stays awake until {until}" if minutes else "The computer stays awake until you press Stop"), "job": job.public(ctx.en)}
