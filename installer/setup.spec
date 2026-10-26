# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec of the MemuAI Setup stub (also the installed uninstaller).

A normal one-file, windowed exe: Python + the installer engine + pywebview
(WinForms + Edge WebView2) + the HTML/WebGL interface in ``installer_ui``.
The program payload is NOT inside it: ``installer/build_setup.py`` appends the
compressed program files after the finished exe (see ``installer/payload.py``).

Driven by environment variables set by build_setup.py:
MEMUAI_SETUP_NAME, MEMUAI_SETUP_ICON, MEMUAI_SETUP_VERSION_FILE.
No UPX, no custom bootloader, no obfuscation; the manifest is the standard
``asInvoker`` one — Setup installs per user and never asks for elevation.
"""
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

ROOT = Path(SPECPATH).resolve().parent
UI = ROOT / "installer" / "ui"
NAME = os.environ.get("MEMUAI_SETUP_NAME", "MemuAI-Setup-stub")
ICON = os.environ.get("MEMUAI_SETUP_ICON", str(ROOT / "memuai-icon.ico"))
VERSION_FILE = os.environ.get("MEMUAI_SETUP_VERSION_FILE") or None

datas = []
for path in sorted(UI.rglob("*")):
    if path.is_file() and path.name != "mock.js" and "__pycache__" not in path.parts:
        datas.append((str(path), str(Path("installer_ui") / path.parent.relative_to(UI))))
datas += collect_data_files("webview")
datas += copy_metadata("psutil")
binaries = collect_dynamic_libs("webview")

a = Analysis(
    [str(ROOT / "installer" / "run_setup.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["clr", "clr_loader", "pythonnet", "bottle", "proxy_tools",
                   "webview.platforms.winforms", "webview.platforms.edgechromium"],
    hookspath=[],
    runtime_hooks=[],

    excludes=["app", "tkinter", "numpy", "fastapi", "uvicorn", "starlette", "pydantic", "curl_cffi", "httpx",
              "PIL", "onnxruntime", "faster_whisper", "ctranslate2", "piper", "anthropic", "rich",
              "prompt_toolkit", "pystray", "pypdf", "winotify", "websockets"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon=ICON,
    version=VERSION_FILE,
    uac_admin=False,
)
