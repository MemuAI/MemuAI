"""Make ``memucode`` a command in every new terminal.

The Windows installer adds the MemuCode folder to the user's PATH itself;
this module does the same on demand (``memucode install`` / ``/install``) and
covers running from source by writing a small ``memucode.cmd`` shim.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Tuple

from .i18n import tr


def _target_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "MemuAI" / "bin"
    return base


def _write_shim(folder: Path) -> None:
    if getattr(sys, "frozen", False):
        return
    folder.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).resolve().parents[2] / "memucode.py"
    python = Path(sys.executable)
    if os.name == "nt":
        (folder / "memucode.cmd").write_text(f'@echo off\r\n"{python}" "{script}" %*\r\n', encoding="utf-8")
    else:
        shim = folder / "memucode"
        shim.write_text(f'#!/bin/sh\nexec "{python}" "{script}" "$@"\n', encoding="utf-8")
        shim.chmod(0o755)


def _user_path_windows() -> str:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as key:
        try:
            value, _kind = winreg.QueryValueEx(key, "Path")
            return str(value)
        except FileNotFoundError:
            return ""


def _set_user_path_windows(value: str) -> None:
    import ctypes
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, value)
    try:  # tell Explorer and new terminals that the environment changed
        result = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 3000, ctypes.byref(result))
    except Exception:
        pass


def is_installed() -> bool:
    return shutil.which("memucode") is not None or _on_user_path(_target_dir())


def _on_user_path(folder: Path) -> bool:
    if os.name != "nt":
        return str(folder) in os.environ.get("PATH", "").split(os.pathsep)
    try:
        entries = [os.path.normcase(os.path.expandvars(item.strip()).rstrip("\\")) for item in _user_path_windows().split(";")]
    except OSError:
        return False
    return os.path.normcase(str(folder).rstrip("\\")) in entries


def install_command() -> Tuple[bool, str]:
    folder = _target_dir()
    try:
        _write_shim(folder)
    except OSError as exc:
        return False, tr(f"Не удалось создать ярлык команды: {exc}", f"Could not write the command shim: {exc}")
    if os.name != "nt":
        local_bin = Path.home() / ".local" / "bin"
        local_bin.mkdir(parents=True, exist_ok=True)
        if not getattr(sys, "frozen", False):
            _write_shim(local_bin)
        return True, tr(f"Готово: {local_bin}/memucode (убедитесь, что ~/.local/bin в PATH)",
                        f"Done: {local_bin}/memucode (make sure ~/.local/bin is on PATH)")
    if _on_user_path(folder):
        return True, tr(f"Уже в PATH: {folder}", f"Already on PATH: {folder}")
    try:
        current = _user_path_windows()
        value = (current.rstrip(";") + ";" if current else "") + str(folder)
        _set_user_path_windows(value)
    except OSError as exc:
        return False, tr(f"Не удалось изменить PATH: {exc}", f"Could not change PATH: {exc}")
    return True, tr(f"Добавлено в PATH: {folder}. Откройте новый терминал и наберите memucode.",
                    f"Added to PATH: {folder}. Open a new terminal and type memucode.")


def uninstall_command() -> Tuple[bool, str]:
    if os.name != "nt":
        shim = Path.home() / ".local" / "bin" / "memucode"
        if shim.is_file() and not getattr(sys, "frozen", False):
            shim.unlink()
            return True, "Removed the MemuCode command. Personal data was kept."
        return False, "Remove the CLI program with your package manager. Personal data is kept."
    folder = os.path.normcase(str(_target_dir()).rstrip("\\"))
    try:
        entries = [item for item in _user_path_windows().split(";") if item.strip()
                   and os.path.normcase(os.path.expandvars(item.strip()).rstrip("\\")) != folder]
        _set_user_path_windows(";".join(entries))
    except OSError as exc:
        return False, str(exc)
    if not getattr(sys, "frozen", False):
        shim = _target_dir() / "memucode.cmd"
        try:
            shim.unlink(missing_ok=True)
        except OSError as exc:
            return False, f"Removed from PATH, but could not remove command shim: {exc}"
    return True, ("Removed the command from PATH. Run MemuAI Setup and uncheck MemuCode CLI to remove its binaries. "
                  "Chats, skills and settings are kept.")
