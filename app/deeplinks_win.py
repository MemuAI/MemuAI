"""Round 35 (protocol): the ``memuai://`` protocol handler in the Windows registry (per user, HKCU).

``HKCU\\Software\\Classes\\memuai``                     (default) = "URL:MemuAI", "URL Protocol" = ""
``HKCU\\Software\\Classes\\memuai\\DefaultIcon``         (default) = "<MemuAI.exe>,0"
``HKCU\\Software\\Classes\\memuai\\shell\\open\\command`` (default) = "\"<MemuAI.exe>\" --deeplink \"%1\""

The installer writes the same keys (installer/products.py ``url_protocols``) and removes them on uninstall;
MemuAI repairs them at start (:func:`self_heal`) when they are missing or point at another exe — e.g.
after the folder was moved.  Only the frozen MemuAI.exe registers itself (a source checkout never
hijacks the handler).  ``winreg`` is injectable for tests (``_reg``).
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("memuai.deeplinks")

KEY = r"Software\Classes\memuai"
SUBKEYS = (r"shell\open\command", r"shell\open", "shell", "DefaultIcon")
ARG = "--deeplink"

_reg: Any = None

def _winreg():
    global _reg
    if _reg is None:
        import winreg  # noqa: PLC0415 - Windows only
        _reg = winreg
    return _reg

def exe_path() -> Optional[str]:
    if getattr(sys, "frozen", False) and Path(sys.executable).name.lower() == "memuai.exe":
        return str(Path(sys.executable).resolve())
    return None

def command_for(exe: str) -> str:
    return f'"{exe}" {ARG} "%1"'

def _read(subkey: str = "") -> Optional[str]:
    w = _winreg()
    try:
        with w.OpenKey(w.HKEY_CURRENT_USER, KEY + ("\\" + subkey if subkey else ""), 0, w.KEY_READ) as handle:
            return str(w.QueryValueEx(handle, "")[0])
    except OSError:
        return None

def _write(subkey: str, name: str, value: str) -> None:
    w = _winreg()
    with w.CreateKeyEx(w.HKEY_CURRENT_USER, KEY + ("\\" + subkey if subkey else ""), 0, w.KEY_WRITE) as handle:
        w.SetValueEx(handle, name, 0, w.REG_SZ, value)

def status() -> Dict[str, Any]:
    command = _read(r"shell\open\command")
    exe = exe_path()
    return {"registered": bool(command), "command": command or "", "exe": exe or "",
            "current": bool(command and exe and command.strip() == command_for(exe)), "frozen": bool(exe)}

def register(exe: Optional[str] = None) -> Dict[str, Any]:
    exe = exe or exe_path()
    if not exe:
        return {"ok": False, "error": "not_frozen", **status()}
    _write("", "", "URL:MemuAI")
    _write("", "URL Protocol", "")
    _write("DefaultIcon", "", f'"{exe}",0')
    _write(r"shell\open\command", "", command_for(exe))
    log.info("memuai:// registered for %s", exe)
    return {"ok": True, **status()}

def unregister() -> Dict[str, Any]:
    w = _winreg()
    for sub in SUBKEYS + ("",):
        try:
            w.DeleteKey(w.HKEY_CURRENT_USER, KEY + ("\\" + sub if sub else ""))
        except OSError:
            pass
    log.info("memuai:// unregistered")
    return {"ok": True, **status()}

def self_heal() -> Dict[str, Any]:
    """At MemuAI start: repair a missing / foreign handler.  Respects «links off» (then it does nothing)."""
    if sys.platform != "win32" or not exe_path():
        return {"ok": False, "skipped": True}
    try:
        from .deeplinks import load_settings
        if not load_settings().get("enabled", True):
            return {"ok": True, "skipped": True}
    except Exception:  # noqa: BLE001
        pass
    try:
        state = status()
        if state["current"]:
            return {"ok": True, "repaired": False}
        return {**register(), "repaired": True}
    except OSError as exc:
        log.warning("memuai:// self-heal failed: %s", exc)
        return {"ok": False, "error": str(exc)}

def link_from_argv(argv) -> str:
    """``MemuAI.exe --deeplink "memuai://…"`` (or a bare ``memuai://…`` argument) → the link, else ''."""
    args = list(argv or [])
    for index, value in enumerate(args):
        if value == ARG and index + 1 < len(args):
            return str(args[index + 1])
        if value.startswith(ARG + "="):
            return value.split("=", 1)[1]
    for value in args:
        if str(value).lower().startswith("memuai:"):
            return str(value)
    return ""

if os.name != "nt":  # pragma: no cover - documentation for non-Windows imports
    __doc__ += "\n(This platform has no Windows registry: functions need an injected ``_reg``.)"
