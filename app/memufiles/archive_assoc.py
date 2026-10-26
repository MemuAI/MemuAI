"""MemuFiles · «Open archives in MemuFiles» (round 41, r41-files) — a per-user file association, no admin rights.

``sync(True)`` writes under ``HKCU\\Software\\Classes``:

* ProgID ``MemuFiles.Archive`` — name, icon, ``shell\\open\\command = "MemuFiles.exe" --archive "%1"`` (MemuFiles opens
  the archive like a folder; a running window takes it over in one loopback request, see ``desktop.main``);
* for every extension of :data:`archive.ASSOC_EXT`: the old default value of ``.ext`` is saved in
  ``<data>/memufiles/shell/archives.json``, the default becomes ``MemuFiles.Archive`` and the ProgID is added to
  ``.ext\\OpenWithProgids`` (MemuFiles is also listed in «Open with»);
* ``Software\\RegisteredApplications\\MemuFiles`` → ``Software\\MemuAI\\MemuFiles\\Capabilities`` — MemuFiles shows up in
  Settings → Apps → Default apps, where Windows lets the user confirm an extension a program already owns.

An extension the user once chose another app for in Windows (``FileExts\\.ext\\UserChoice``, hash-protected) keeps that
app — Windows doesn't let programs override it; :func:`status` lists those (``choice``) and the page offers the
Default apps page. ``sync(False)`` restores the saved defaults and removes everything written. ``SHChangeNotify``
after each change. Tests plug a fake registry through :func:`default_shell.set_registry`.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

from . import default_shell
from .archive import ASSOC_EXT
from .paths import read_json, write_json

CLASSES = r"Software\Classes"
PROGID = "MemuFiles.Archive"
CAPS = r"Software\MemuAI\MemuFiles\Capabilities"
REGAPPS = r"Software\RegisteredApplications"
FILEEXTS = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts"
REG_SZ = 1

def _state_path():
    return default_shell.shell_dir() / "archives.json"

def _exe() -> Optional[str]:
    exe = default_shell.current_exe()
    if exe:
        return exe
    if not getattr(sys, "frozen", False):
        script = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "memufiles.py")
        if os.path.isfile(script):
            return f'{sys.executable}" "{script}'
    return None

def command(exe: str) -> str:
    return f'"{exe}" --archive "%1"'

def _lang() -> str:
    try:
        return default_shell._lang()
    except Exception:  # noqa: BLE001
        return "ru"

def status() -> Dict[str, Any]:
    if not default_shell.available():
        return {"available": False, "on": False, "extensions": {}}
    reg = default_shell._reg()
    exts: Dict[str, str] = {}
    for ext in ASSOC_EXT:
        got = reg.get(CLASSES + "\\" + ext, "")
        choice = reg.get(FILEEXTS + "\\" + ext + r"\UserChoice", "ProgId")
        if choice and choice[1] != PROGID:
            exts[ext] = "choice"
        elif got and got[1] == PROGID:
            exts[ext] = "on"
        else:
            exts[ext] = "off"
    on = any(v == "on" for v in exts.values())
    return {"available": True, "on": on, "extensions": exts,
            "choice": [e for e, v in exts.items() if v == "choice"]}

def sync(enabled: bool) -> Dict[str, Any]:
    if not default_shell.available():
        return {"available": False, "on": False}
    if enabled:
        exe = _exe()
        if not exe:
            return {"available": True, "on": False, "error": "no_exe"}
        _enable(exe)
    else:
        _disable()
    return status()

def _enable(exe: str) -> None:
    reg = default_shell._reg()
    state = read_json(_state_path(), {})
    saved: Dict[str, Any] = state.get("previous") or {}
    ru = _lang() == "ru"
    title = "Архив (MemuFiles)" if ru else "Archive (MemuFiles)"
    icon_path = exe.split('"')[0]
    rows: List[tuple] = [
        (CLASSES + "\\" + PROGID, "", title),
        (CLASSES + "\\" + PROGID, "FriendlyTypeName", title),
        (CLASSES + "\\" + PROGID + r"\DefaultIcon", "", f'"{icon_path}",0'),
        (CLASSES + "\\" + PROGID + r"\shell", "", "open"),
        (CLASSES + "\\" + PROGID + r"\shell\open", "", "Открыть в MemuFiles" if ru else "Open in MemuFiles"),
        (CLASSES + "\\" + PROGID + r"\shell\open\command", "", command(exe)),
        (CAPS, "ApplicationName", "MemuFiles"),
        (CAPS, "ApplicationDescription", "Файловый менеджер MemuAI" if ru else "MemuAI file manager"),
        (REGAPPS, "MemuFiles", CAPS),
    ]
    for ext in ASSOC_EXT:
        key = CLASSES + "\\" + ext
        got = reg.get(key, "")
        if ext not in saved:
            saved[ext] = got[1] if got and got[1] != PROGID else None
        rows += [(key, "", PROGID), (key + r"\OpenWithProgids", PROGID, ""), (CAPS + r"\FileAssociations", ext, PROGID)]
    for key, name, data in rows:
        reg.set(key, name, REG_SZ, data)
    write_json(_state_path(), {"previous": saved, "exe": exe})
    reg.notify()

def _disable() -> None:
    reg = default_shell._reg()
    state = read_json(_state_path(), {})
    saved: Dict[str, Any] = state.get("previous") or {}
    for ext in ASSOC_EXT:
        key = CLASSES + "\\" + ext
        got = reg.get(key, "")
        if got and got[1] == PROGID:
            old = saved.get(ext)
            if old:
                reg.set(key, "", REG_SZ, old)
            else:
                reg.delete_value(key, "")
        reg.delete_value(key + r"\OpenWithProgids", PROGID)
        for path in (key + r"\OpenWithProgids", key):
            if reg.is_empty(path):
                reg.delete_tree(path)
    reg.delete_tree(CLASSES + "\\" + PROGID)
    reg.delete_value(REGAPPS, "MemuFiles")
    reg.delete_tree(r"Software\MemuAI\MemuFiles")
    if reg.is_empty(r"Software\MemuAI"):
        reg.delete_tree(r"Software\MemuAI")
    write_json(_state_path(), {"previous": {}})
    reg.notify()
