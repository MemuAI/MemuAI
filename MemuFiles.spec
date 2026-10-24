# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['memufiles.py'],
    pathex=[],
    binaries=[],
    datas=[('app/memufiles/static', 'app/memufiles/static'), ('memufiles-icon.png', '.')],
    hiddenimports=['app.memufiles', 'app.memufiles.server', 'app.memufiles.desktop', 'app.memufiles.crash', 'app.memufiles.paths', 'app.memufiles.settings', 'app.memufiles.settings_schema', 'app.memufiles.fsops', 'app.memufiles.trash', 'app.memufiles.opqueue', 'app.memufiles.batchrename', 'app.memufiles.search', 'app.memufiles.jobs', 'app.memufiles.preview', 'app.memufiles.assistant', 'app.memufiles.textedit', 'app.memufiles.clip', 'app.memufiles.openwith', 'app.win_titlebar', 'app.memufiles.extras', 'app.memufiles.organize', 'app.memufiles.feat40', 'app.memufiles.taskbar', 'app.memufiles.archive', 'app.memufiles.archive_assoc', 'app.memunote', 'app.memunote.ai', 'app.memunote.paths', 'app.memunote.desktop', 'app.memunote.winsys', 'app.memunote.crash', 'app.model_names', 'app.memory_v2', 'app.storage', 'app.token_estimate', 'app.fsindex', 'app.fsindex_query', 'app.fsindex_win', 'app.themes', 'app.version', 'clr', 'PIL', 'PIL.Image'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['app.websearch', 'app.config', 'app.pgdata', 'app.providers', 'app.quick_files', 'app.quick_apps', 'app.inbox_hooks', 'curl_cffi', 'fastapi', 'uvicorn', 'starlette', 'pydantic', 'httpx', 'faster_whisper', 'ctranslate2', 'av', 'onnxruntime', 'tokenizers', 'huggingface_hub', 'hf_xet', 'numpy', 'piper', 'vosk', 'tkinter', 'pystray', 'spylls'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MemuFiles',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='E:/MemuAI/memufiles-version.txt',
    icon=['E:/MemuAI/memufiles-icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='MemuFiles',
)
