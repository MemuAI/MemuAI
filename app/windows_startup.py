"""Per-user Windows startup registration for the MemuAI chat client."""
from __future__ import annotations

import os
import sys
from pathlib import Path

VALUE_NAME = "MemuAI"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

def executable_path() -> Path | None:
    if os.name != "nt":
        return None
    if getattr(sys, "frozen", False):
        candidate = Path(sys.executable).resolve()
    else:
        program_dir = os.environ.get("MEMUAI_PROGRAM_DIR", "")
        candidate = (Path(program_dir) / "MemuAI.exe").resolve() if program_dir else Path()
    return candidate if candidate.is_file() and candidate.name.lower() == "memuai.exe" else None

def status() -> dict[str, object]:
    candidate = executable_path()
    if os.name != "nt":
        return {"supported": False, "enabled": False, "reason": "Требуется Windows"}
    import winreg
    current = ""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            current = str(winreg.QueryValueEx(key, VALUE_NAME)[0] or "")
    except OSError:
        pass
    expected = f'"{candidate}"' if candidate else ""
    return {"supported": candidate is not None, "enabled": bool(expected and current == expected),
            "path": str(candidate or "")}

def set_enabled(enabled: bool) -> dict[str, object]:
    if os.name != "nt":
        raise RuntimeError("Автозапуск доступен только в Windows")
    import winreg
    candidate = executable_path()
    if enabled and candidate is None:
        raise RuntimeError("Автозапуск можно включить после установки MemuAI")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, f'"{candidate}"')
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
    return status()
