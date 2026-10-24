# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('app/memunote/static', 'app/memunote/static'), ('memunote-icon.png', '.'), ('app/memunote/dicts', 'app/memunote/dicts')]
binaries = []
hiddenimports = ['app.memunote', 'app.memunote.server', 'app.memunote.desktop', 'app.memunote.ai', 'app.memunote.prompts', 'app.memunote.edits', 'app.memunote.settings', 'app.memunote.settings_schema', 'app.memunote.paths', 'app.memunote.winsys', 'app.memunote.formats', 'app.memunote.formats.textio', 'app.memunote.formats.richhtml', 'app.memunote.formats.docx', 'app.memunote.formats.odt', 'app.memunote.formats.rtf', 'app.memunote.formats.pdftools', 'app.memunote.formats.sheets', 'app.memunote.formats.books', 'app.memunote.crash', 'app.memory_v2', 'app.memunote.slides_api', 'app.storage', 'app.themes', 'app.version', 'clr', 'pypdf', 'app.memunote.photo', 'app.memunote.formats.images', 'app.memunote.formats.psd', 'PIL', 'PIL.Image', 'app.memunote.runner', 'app.memunote.spell', 'app.spellcheck', 'app.model_names', 'app.memunote.bigdocs_api', 'app.bigdocs', 'app.token_estimate', 'app.memunote.sheets_api', 'app.memunote.formats.xlsx_edit']
tmp_ret = collect_all('spylls')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['memunote.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['app.websearch', 'app.config', 'app.pgdata', 'app.providers', 'app.inbox_hooks', 'httpx._main', 'curl_cffi', 'fastapi', 'uvicorn', 'starlette', 'pydantic', 'faster_whisper', 'ctranslate2', 'av', 'onnxruntime', 'tokenizers', 'huggingface_hub', 'hf_xet', 'numpy', 'piper', 'vosk', 'tkinter', 'pystray'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MemuNote',
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
    version='E:/MemuAI/memunote-version.txt',
    icon=['E:/MemuAI/memunote-icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='MemuNote',
)
