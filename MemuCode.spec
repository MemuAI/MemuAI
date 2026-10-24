# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('app/docs', 'app/docs')]
binaries = []
hiddenimports = ['websockets', 'pypdf', 'winotify', 'PIL.Image', 'PIL.ImageGrab', 'PIL.ImageDraw', 'PIL.ImageFont', 'app.cli.vision', 'app.instant_answers', 'app.cli.commands.config_cmds', 'app.cli.commands.info_cmds', 'app.cli.commands.model_cmds', 'app.cli.commands.session_cmds', 'app.cli.commands.work_cmds', 'app.cli.commands.dashboard_cmds', 'app.cli.commands.decisions_cmds', 'app.cli.commands.foreign_cmds', 'app.cli.commands.import_cmds', 'app.cli.commands.routing_cmds', 'app.cli.commands.schedule_cmds', 'app.cli.commands.skill_cmds', 'app.cli.foreign', 'app.cli.foreign.agy', 'app.cli.foreign.claude', 'app.cli.foreign.codex', 'app.cli.foreign.configs', 'app.cli.foreign.cursor', 'app.cli.foreign.gemini', 'app.cli.foreign.grok', 'app.cli.foreign.handoff', 'app.cli.foreign.importer', 'app.cli.foreign.model', 'app.cli.foreign.opencode', 'app.cli.foreign.protobuf', 'app.cli.schedule', 'app.cli.triage', 'app.cli.risk', 'app.cli.modelmeta', 'app.cli.skillkit', 'app.cli.editor', 'app.cli.tools.skills', 'sqlite3', 'tomllib', 'app.cli.remote', 'app.cli.remote_host', 'app.memory_v2', 'app.stuck_detector', 'app.answer_guard', 'app.cli.errors', 'app.cli.autocontinue', 'app.cli.ratelimit', 'app.cli.selfdoc', 'app.cli.commands.autocontinue_cmds', 'app.cli.commands.guide_cmds', 'app.model_names', 'app.reasoning_levels']
tmp_ret = collect_all('prompt_toolkit')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('rich')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pygments')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('anthropic')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('curl_cffi')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('wcwidth')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['memucode.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['faster_whisper', 'ctranslate2', 'av', 'onnxruntime', 'tokenizers', 'huggingface_hub', 'hf_xet', 'numpy', 'piper', 'clr', 'pythonnet', 'clr_loader', 'prompt_toolkit.contrib.ssh'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MemuCode',
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
    version='E:/MemuAI/memucode-version.txt',
    icon=['E:/MemuAI/memuai-icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='MemuCode',
)
