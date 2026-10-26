"""Installed-app index and fuzzy name resolution for «открой телеграм».

Sources (Windows), merged and de-duplicated by folded name:

* Start menu shortcuts (``.lnk``/``.url``) of the user and of all users;
* the ``App Paths`` registry key (HKCU and HKLM) — programs that register an
  executable name (``chrome.exe``, ``winword.exe``);
* ``Get-StartApps`` (PowerShell) — every Start entry including Store/UWP apps,
  launched through ``shell:AppsFolder\\<AppID>``;
* a built-in table of Windows tools with Russian and English names.

:func:`resolve` is pure (tested on Linux with a fake index): exact folded name,
alias table (хром → chrome, телега → telegram, ворд → word…), transliteration,
token prefix and ``difflib`` similarity.
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Iterable, List, Optional

from .assistant_intents import fold, similarity, translit

log = logging.getLogger("memuai.assistant.apps")

BUILTIN_APPS: List[Dict[str, Any]] = [
    {"name": "Блокнот", "aliases": ["notepad", "блокнот", "нотпад", "нотепад"], "target": "notepad.exe", "exe": "notepad.exe"},
    {"name": "Калькулятор", "aliases": ["calculator", "calc", "калькулятор", "калк"], "target": "calc.exe", "exe": "calculatorapp.exe"},
    {"name": "Paint", "aliases": ["paint", "mspaint", "пейнт", "паинт", "рисовалка"], "target": "mspaint.exe", "exe": "mspaint.exe"},
    {"name": "Проводник", "aliases": ["explorer", "file explorer", "проводник", "мой компьютер", "этот компьютер", "this pc"],
     "target": "explorer.exe", "exe": "explorer.exe"},
    {"name": "Диспетчер задач", "aliases": ["task manager", "taskmgr", "диспетчер задач", "диспетчер"], "target": "taskmgr.exe", "exe": "taskmgr.exe"},
    {"name": "Командная строка", "aliases": ["cmd", "command prompt", "командная строка", "консоль"], "target": "cmd.exe", "exe": "cmd.exe"},
    {"name": "PowerShell", "aliases": ["powershell", "павершелл", "пауэршелл", "повершел"], "target": "powershell.exe", "exe": "powershell.exe"},
    {"name": "Терминал", "aliases": ["terminal", "windows terminal", "терминал", "wt"], "target": "wt.exe", "exe": "windowsterminal.exe"},
    {"name": "Панель управления", "aliases": ["control panel", "панель управления"], "target": "control.exe", "exe": "control.exe"},
    {"name": "Параметры", "aliases": ["settings", "windows settings", "параметры", "параметры windows", "настройки windows"],
     "target": "ms-settings:", "exe": "systemsettings.exe"},
    {"name": "Ножницы", "aliases": ["snipping tool", "ножницы", "snip"], "target": "ms-screenclip:", "exe": "snippingtool.exe"},
    {"name": "Редактор реестра", "aliases": ["regedit", "registry editor", "редактор реестра", "реестр"], "target": "regedit.exe", "exe": "regedit.exe"},
    {"name": "Диспетчер устройств", "aliases": ["device manager", "диспетчер устройств"], "target": "devmgmt.msc", "exe": "mmc.exe"},
    {"name": "Службы", "aliases": ["services", "службы"], "target": "services.msc", "exe": "mmc.exe"},
    {"name": "Управление дисками", "aliases": ["disk management", "управление дисками"], "target": "diskmgmt.msc", "exe": "mmc.exe"},
    {"name": "Монитор ресурсов", "aliases": ["resource monitor", "монитор ресурсов"], "target": "resmon.exe", "exe": "resmon.exe"},
    {"name": "Экранная клавиатура", "aliases": ["on-screen keyboard", "osk", "экранная клавиатура"], "target": "osk.exe", "exe": "osk.exe"},
    {"name": "Лупа", "aliases": ["magnifier", "лупа"], "target": "magnify.exe", "exe": "magnify.exe"},
    {"name": "Камера", "aliases": ["camera", "камера"], "target": "microsoft.windows.camera:", "exe": "windowscamera.exe"},
    {"name": "Почта", "aliases": ["mail", "outlook mail"], "target": "outlookmail:", "exe": "olk.exe"},
    {"name": "Календарь", "aliases": ["calendar", "календарь"], "target": "outlookcal:", "exe": "olk.exe"},
    {"name": "Microsoft Store", "aliases": ["store", "microsoft store", "магазин", "магазин приложений"], "target": "ms-windows-store:", "exe": "winstore.app.exe"},
    {"name": "Корзина", "aliases": ["recycle bin", "корзина", "корзину"], "target": "shell:RecycleBinFolder", "exe": "explorer.exe"},
    {"name": "MemuAI", "aliases": ["memuai", "мему", "memu", "мемуаи"], "target": "memuai:", "exe": "memuai.exe"},
]

ALIASES: Dict[str, List[str]] = {
    "chrome": ["хром", "гугл хром", "google chrome", "хроме", "chrome"],
    "telegram": ["телеграм", "телеграмм", "телега", "тг", "telegram desktop", "телеграф"],
    "yandex": ["яндекс", "яндекс браузер", "yandex browser", "браузер яндекс"],
    "firefox": ["firefox", "фаерфокс", "файрфокс", "мозилла", "мозила", "огненная лиса"],
    "edge": ["эдж", "edge", "microsoft edge", "майкрософт эдж"],
    "opera": ["опера", "opera gx", "опера gx"],
    "brave": ["брейв", "brave"],
    "visual studio code": ["vs code", "vscode", "вс код", "вскод", "код", "visual studio code", "вижуал студио код", "code"],
    "visual studio": ["visual studio", "вижуал студио", "студия"],
    "word": ["ворд", "word", "microsoft word", "майкрософт ворд"],
    "excel": ["эксель", "excel", "ексель", "microsoft excel"],
    "powerpoint": ["повер поинт", "пауэрпоинт", "поверпоинт", "powerpoint", "презентации"],
    "outlook": ["аутлук", "outlook"],
    "onenote": ["ван ноут", "onenote"],
    "teams": ["тимс", "teams", "microsoft teams"],
    "discord": ["дискорд", "discord", "дс"],
    "steam": ["стим", "steam"],
    "epic games": ["эпик", "epic games", "эпик геймс", "epic games launcher"],
    "spotify": ["спотифай", "spotify", "спотик"],
    "яндекс музыка": ["яндекс музыка", "yandex music"],
    "obs studio": ["обс", "obs", "obs studio"],
    "zoom": ["зум", "zoom"],
    "skype": ["скайп", "skype"],
    "viber": ["вайбер", "viber"],
    "whatsapp": ["ватсап", "вотсап", "whatsapp", "вацап"],
    "photoshop": ["фотошоп", "photoshop", "adobe photoshop"],
    "premiere pro": ["премьер", "premiere", "премьер про"],
    "after effects": ["афтер эффектс", "after effects"],
    "illustrator": ["иллюстратор", "illustrator"],
    "figma": ["фигма", "figma"],
    "notion": ["ноушен", "notion"],
    "obsidian": ["обсидиан", "obsidian"],
    "slack": ["слак", "slack"],
    "vlc media player": ["vlc", "влс", "влц"],
    "pycharm": ["пайчарм", "pycharm"],
    "intellij idea": ["идея", "intellij", "intellij idea"],
    "android studio": ["андроид студио", "android studio"],
    "blender": ["блендер", "blender"],
    "unity hub": ["юнити", "unity"],
    "battle.net": ["батлнет", "battle net", "battle.net"],
    "epic": ["эпик геймс"],
    "minecraft": ["майнкрафт", "minecraft"],
    "anydesk": ["энидеск", "anydesk"],
    "teamviewer": ["тимвьювер", "teamviewer"],
    "qbittorrent": ["кубиторрент", "торрент", "qbittorrent"],
    "7-zip": ["7zip", "севензип", "7-zip"],
    "winrar": ["винрар", "winrar"],
    "notepad++": ["нотпад плюс плюс", "notepad++", "нотепад плюс"],
    "git bash": ["гит баш", "git bash"],
    "docker desktop": ["докер", "docker"],
    "postman": ["постман", "postman"],
    "cursor": ["курсор", "cursor"],
    "яндекс телемост": ["телемост"],
    "memucode": ["мемукод", "memucode"],
    "memuai proxy": ["прокси", "memuai proxy", "мему прокси"],
}

_CACHE: Dict[str, Any] = {"at": 0.0, "items": [], "building": False}
_LOCK = threading.Lock()
CACHE_TTL = 15 * 60

def _entry(name: str, kind: str, target: str, *, exe: str = "", args: str = "", aliases: Iterable[str] = ()) -> Dict[str, Any]:
    return {"name": name, "kind": kind, "target": target, "exe": exe.lower(), "args": args,
            "aliases": [a for a in aliases if a], "key": fold(name)}

_JUNK = re.compile(r"\b(?:uninstall|удалить|удаление|readme|help|справка|documentation|документация|license|"
                   r"лицензия|release notes|website|веб-сайт|manual|руководство|changelog|support|поддержка)\b", re.I)

def is_junk(name: str) -> bool:
    return bool(_JUNK.search(name or ""))

def merge_index(*groups: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep the first entry per folded name (order of groups = priority)."""
    seen: Dict[str, Dict[str, Any]] = {}
    for group in groups:
        for row in group or []:
            if not isinstance(row, dict) or not row.get("name") or is_junk(row["name"]):
                continue
            key = row.get("key") or fold(row["name"])
            if not key:
                continue
            if key not in seen:
                seen[key] = dict(row, key=key)
            else:

                prev = seen[key]
                if not prev.get("exe") and row.get("exe"):
                    prev["exe"] = row["exe"]
                prev["aliases"] = list(dict.fromkeys([*prev.get("aliases", []), *row.get("aliases", [])]))
    return list(seen.values())

def builtin_entries() -> List[Dict[str, Any]]:
    return [_entry(row["name"], "builtin", row["target"], exe=row.get("exe", ""), aliases=row["aliases"])
            for row in BUILTIN_APPS]

_RU_KEYS = "йцукенгшщзхъфывапролджэячсмитьбю"
_EN_KEYS = "qwertyuiop[]asdfghjkl;'zxcvbnm,."
_RU2EN = {ord(a): b for a, b in zip(_RU_KEYS, _EN_KEYS)}
_EN2RU = {ord(b): a for a, b in zip(_RU_KEYS, _EN_KEYS)}

_NAME_NOISE = re.compile(r"^(?:(?:это|эту|этот|мой|мою|моё|свой|свою|the|my|this)\s+)?(?:окно|окна|окошко|приложение|"
                         r"приложения|программу|программа|программы|прогу|процесс|утилиту|клиент|апп|app|application|"
                         r"program|window|the app|the program)\s+", re.I)

def clean_name(spoken: Any) -> str:
    """The app name the user said, without «окно / приложение / программу / the app» around it and quotes."""
    text = re.sub(r"\s+", " ", str(spoken or "").strip().strip("\"'«»“”„`")).strip()
    for _ in range(2):
        text = _NAME_NOISE.sub("", text).strip()
    text = re.sub(r"\s+(?:окно|окна|app|window|программу|приложение)$", "", text, flags=re.I).strip()
    return text.strip("\"'«»“”„` ") or str(spoken or "").strip()

def phonetic(text: Any) -> str:
    """A sound-alike key over the transliterated name: «инси» and «incy» → ``insi``; «спотифай»/«spotify» close.

    Russian users write Latin names the way they sound; the Latin spelling rules that differ from the sound
    (c before e/i/y, ck, ph, qu, x, w, y as a vowel, doubled letters) are folded on both sides."""
    s = fold(text)
    if not s:
        return ""
    for a, b in (("sch", "sh"), ("tch", "ch"), ("dzh", "j"), ("zh", "j"), ("ck", "k"), ("ph", "f"), ("qu", "kv"),
                 ("x", "ks"), ("w", "v"), ("ee", "i"), ("oo", "u"), ("ay", "ai"), ("ey", "ei"), ("kh", "h")):
        s = s.replace(a, b)
    s = re.sub(r"c(?=[eiy])", "s", s)
    s = s.replace("ch", "\x01").replace("c", "k").replace("\x01", "ch")
    s = s.replace("y", "i")
    s = re.sub(r"(.)\1+", r"\1", s)
    return s

def layout_swap(text: Any) -> str:
    """A name typed in the wrong keyboard layout: «штсн» → «incy», «ntktuhfv» → «телеграм» ("" when not applicable)."""
    raw = str(text or "").strip().lower()
    if not raw or " " in raw:
        return ""
    if re.fullmatch(r"[а-яё]+", raw):
        out = raw.replace("ё", "`").translate(_RU2EN)
        return out if re.fullmatch(r"[a-z]+", out) else ""
    if re.fullmatch(r"[a-z\[\];',.`]+", raw):
        out = raw.translate(_EN2RU)
        return out if re.fullmatch(r"[а-яё]+", out) and out != raw else ""
    return ""

def _candidates(spoken: str) -> List[str]:
    """The spoken name and every canonical name its alias table points to."""
    text = re.sub(r"\s+", " ", clean_name(spoken).lower().replace("ё", "е"))
    text = re.sub(r"^(?:приложение|программу|программа|app|the)\s+", "", text)
    out = [text]
    keys = {fold(text)}

    if re.fullmatch(r"(?:[а-я]+ )*[а-я]{3,}[ую]", text):
        keys.add(fold(text[:-1] + ("а" if text.endswith("у") else "я")))
    for canonical, words in ALIASES.items():
        if fold(canonical) in keys or any(fold(word) in keys for word in words):
            out.append(canonical)
    translit_text = translit(text)
    if translit_text != text:
        out.append(translit_text)
    return list(dict.fromkeys(out))

def score_entry(entry: Dict[str, Any], spoken: str) -> float:
    best = 0.0
    names = [entry.get("name", ""), *entry.get("aliases", [])]
    exe = entry.get("exe") or ""
    if exe:
        names.append(re.sub(r"\.exe$", "", exe))
    for candidate in _candidates(spoken):
        ckey = fold(candidate)
        if not ckey:
            continue
        for name in names:
            nkey = fold(name)
            if not nkey:
                continue
            if nkey == ckey:
                return 1.0
            score = similarity(name, candidate)

            pa, pb = phonetic(name), phonetic(candidate)
            if pa and pb and len(pb) >= 3:
                if pa == pb:
                    score = max(score, 0.96)
                else:
                    score = max(score, difflib.SequenceMatcher(None, pa, pb).ratio() * 0.97)

            words = [fold(w) for w in re.split(r"[\s\-_.]+", str(name)) if fold(w)]
            if ckey in words:
                score = max(score, 0.93 - 0.01 * max(0, len(words) - 2))
            elif len(ckey) >= 4 and nkey.startswith(ckey):
                score = max(score, 0.88)
            elif len(ckey) >= 5 and ckey in nkey:
                score = max(score, 0.82)
            best = max(best, score)

    swapped = layout_swap(clean_name(spoken))
    if swapped and best < 0.95:
        skey = fold(swapped)
        for name in names:
            words = [fold(w) for w in re.split(r"[\s\-_.]+", str(name)) if fold(w)]
            if skey and (fold(name) == skey or skey in words):
                best = max(best, 0.95)
    return best

def resolve(spoken: str, index: Iterable[Dict[str, Any]], *, threshold: float = 0.78,
            limit: int = 5) -> Dict[str, Any]:
    """``{"match": entry|None, "score", "alternatives": [entries]}`` for a spoken app name."""
    scored = []
    for entry in index or []:
        score = score_entry(entry, spoken)
        if score > 0.5:

            bonus = {"lnk": 0.004, "uwp": 0.003, "apppath": 0.002, "builtin": 0.001}.get(entry.get("kind", ""), 0)
            scored.append((score + bonus, entry))
    scored.sort(key=lambda row: row[0], reverse=True)
    if not scored or scored[0][0] < threshold:
        return {"match": None, "score": round(scored[0][0], 3) if scored else 0.0,
                "alternatives": [row[1] for row in scored[:limit]]}
    return {"match": scored[0][1], "score": round(min(1.0, scored[0][0]), 3),
            "alternatives": [row[1] for row in scored[1:limit]]}

def match_process(spoken: str, exe_name: str, title: str = "") -> float:
    """How well a running process (``chrome.exe`` / window title) matches the spoken app name."""
    stem = re.sub(r"\.exe$", "", str(exe_name or "").lower())
    fake = {"name": stem, "aliases": [title] if title and len(title) < 40 else [], "exe": ""}
    score = score_entry(fake, spoken)
    for canonical, words in ALIASES.items():
        if any(fold(spoken) == fold(w) for w in [canonical, *words]):
            ckey = fold(canonical)
            if ckey and (ckey in fold(stem) or fold(stem) in ckey):
                score = max(score, 0.95)
    known = {"code": "visual studio code", "winword": "word", "powerpnt": "powerpoint", "excel": "excel",
             "msedge": "edge", "browser": "yandex", "telegram": "telegram", "obs64": "obs studio",
             "spotify": "spotify", "discord": "discord", "steam": "steam", "calculatorapp": "калькулятор",
             "notepad": "блокнот", "explorer": "проводник", "windowsterminal": "терминал", "olk": "outlook"}
    if stem in known:
        score = max(score, score_entry({"name": known[stem], "aliases": ALIASES.get(known[stem], []), "exe": ""}, spoken))
    return score

def _startmenu_dirs() -> List[str]:
    dirs = []
    for env, tail in (("APPDATA", r"Microsoft\Windows\Start Menu\Programs"),
                      ("PROGRAMDATA", r"Microsoft\Windows\Start Menu\Programs")):
        base = os.environ.get(env)
        if base:
            dirs.append(os.path.join(base, tail))
    return dirs

def startmenu_entries(dirs: Optional[List[str]] = None, *, max_files: int = 3000) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    count = 0
    for root in dirs if dirs is not None else _startmenu_dirs():
        if not os.path.isdir(root):
            continue
        for folder, _subdirs, files in os.walk(root):
            if folder[len(root):].count(os.sep) > 3:
                continue
            for name in files:
                low = name.lower()
                if not low.endswith((".lnk", ".url", ".appref-ms")):
                    continue
                count += 1
                if count > max_files:
                    return out
                stem = os.path.splitext(name)[0]
                out.append(_entry(stem, "lnk", os.path.join(folder, name)))
    return out

def apppath_entries() -> List[Dict[str, Any]]:
    if sys.platform != "win32":
        return []
    out: List[Dict[str, Any]] = []
    try:
        import winreg
    except Exception:
        return out
    key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, key_path) as root:
                index = 0
                while index < 2000:
                    try:
                        sub = winreg.EnumKey(root, index)
                    except OSError:
                        break
                    index += 1
                    if not sub.lower().endswith(".exe"):
                        continue
                    try:
                        with winreg.OpenKey(root, sub) as key:
                            target, _ = winreg.QueryValueEx(key, "")
                    except OSError:
                        continue
                    target = os.path.expandvars(str(target or "").strip().strip('"'))
                    if not target:
                        continue
                    out.append(_entry(sub[:-4], "apppath", target, exe=sub))
        except OSError:
            continue
    return out

def uwp_entries(timeout: float = 12.0) -> List[Dict[str, Any]]:
    """``Get-StartApps``: every Start menu entry with its AppUserModelID (Store apps included)."""
    if sys.platform != "win32":
        return []
    script = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
              "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress")
    try:
        proc = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                               "-Command", script], capture_output=True, timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        data = json.loads(proc.stdout.decode("utf-8", "replace") or "[]")
    except Exception as exc:
        log.debug("Get-StartApps failed: %s", exc)
        return []
    rows = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    out = []
    for row in rows:
        name, app_id = str(row.get("Name") or ""), str(row.get("AppID") or "")
        if name and app_id:
            out.append(_entry(name, "uwp", "shell:AppsFolder\\" + app_id))
    return out

def build_index() -> List[Dict[str, Any]]:
    return merge_index(builtin_entries(), startmenu_entries(), uwp_entries(), apppath_entries())

def index(refresh: bool = False, *, wait: bool = True) -> List[Dict[str, Any]]:
    """The cached index (rebuilt every ``CACHE_TTL``; the first call builds it synchronously)."""
    now = time.time()
    with _LOCK:
        fresh = _CACHE["items"] and now - _CACHE["at"] < CACHE_TTL and not refresh
        if fresh:
            return list(_CACHE["items"])
        stale = list(_CACHE["items"])
        if _CACHE["building"]:
            return stale or builtin_entries()
        _CACHE["building"] = True

    def build() -> List[Dict[str, Any]]:
        try:
            items = build_index() if sys.platform == "win32" else builtin_entries()
        except Exception as exc:
            log.warning("app index failed: %s", exc)
            items = stale or builtin_entries()
        with _LOCK:
            _CACHE.update(items=items, at=time.time(), building=False)
        return items

    if stale and not wait:
        threading.Thread(target=build, name="memuai-app-index", daemon=True).start()
        return stale
    return build()

def warm() -> None:
    if sys.platform == "win32":
        threading.Thread(target=index, name="memuai-app-index", daemon=True).start()
