# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['memu.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['app.memu_cli', 'app.deeplinks', 'app.deeplinks_routes', 'app.version'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['fastapi', 'uvicorn', 'starlette', 'pydantic', 'httpx', 'curl_cffi', 'PIL', 'numpy', 'tkinter', 'app.config', 'app.pgdata', 'app.providers', 'app.skills_catalog', 'app.memunote_manager', 'app.fsindex_api', 'faster_whisper', 'onnxruntime', 'piper', 'vosk', 'pystray', 'webview', 'clr', 'app.browser_extension_connect'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='memu',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='E:/MemuAI/memu-version.txt',
    icon=['E:/MemuAI/memuai-icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='memu',
)
