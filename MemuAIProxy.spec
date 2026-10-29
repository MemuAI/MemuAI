# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('app/static', 'app/static'), ('app/pgstatic', 'app/pgstatic'), ('app/companion_kb', 'app/companion_kb'), ('app/dfy_agent.py', 'app'), ('app/local_catalog', 'app/local_catalog'), ('app/module_builtins', 'app/module_builtins'), ('app/module_catalog_seed.json', 'app'), ('app/tgapp', 'app/tgapp'), ('icon.png', '.'), ('icon.ico', '.')]
binaries = []
hiddenimports = ['uvicorn.loops.auto', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on', 'websockets', 'pystray', 'pypdf', 'winotify', 'PIL.Image', 'PIL.ImageDraw', 'PIL.ImageOps', 'app.api_budget', 'app.model_refresh', 'app.browser_extension', 'app.browser_extension_chat', 'app.browser_extension_files', 'app.browser_extension_store', 'app.browser_extension_connect', 'app.browser_extension_preferences', 'app.browser_extension_tools', 'app.telegram_use', 'app.telegram_use_arguments', 'app.telegram_use_voice', 'app.voice_downloads', 'app.stt_providers', 'app.panels_proxy_usage', 'app.win_window_theme', 'app.cli.file_cache', 'app.teams', 'app.team_runtime', 'app.teams_api', 'app.memudb', 'app.memudb_api', 'app.memudb_duckdb', 'duckdb', '_duckdb', 'app.module_catalog', 'app.module_catalog_api', 'uvicorn.protocols.http.h11_impl', 'uvicorn.lifespan.off', 'app.reasoning_levels', 'app.reasoning_levels_api', 'app.quota_planner', 'app.composite_models', 'app.composite_models_api', 'app.stream_recovery', 'app.difficulty', 'app.prompt_cache', 'app.web_models', 'app.web_models_api', 'app.model_names', 'app.model_catalog', 'app.model_catalog_api', 'app.tg_webapp', 'app.win_app_identity', 'app.win_com', 'app.tgapp_api', 'app.tgapp_chat', 'app.tgapp_code', 'app.cli.remote', 'app.cli.session', 'app.cli.paths', 'app.telegram_panels', 'app.panels', 'app.panels_local', 'app.panels_options', 'app.panels_layout', 'app.theme_editor', 'app.ask_user', 'app.ask_user_telegram', 'app.mentions', 'app.dfy', 'app.dfy_vault', 'app.stuck_detector', 'app.answer_guard', 'app.dfy_proxy', 'app.dfy_agent', 'app.dfy_remote', 'app.dfy_machine', 'app.tts_providers', 'app.memory_v2', 'app.gdrive_sync', 'app.gdrive_sync_crypto', 'app.gdrive_sync_drive', 'app.google_account', 'app.integrations_gmail', 'app.integrations_notion', 'app.integrations_notion_tools', 'app.oauth_config', 'app.oauth_build', 'app.safe_mode', 'app.tgapp_code_api', 'app.tgapp_extras', 'app.tgapp_weblogin', 'app.tgapp_complete', 'app.telegram_voice', 'app.search_index', 'app.search_api', 'app.chat_tree', 'app.scheduled_messages', 'app.scheduled_messages_runner', 'app.schedule_rules', 'app.schedule_runtime', 'app.schedule_sampler', 'app.schedule_triggers_ext', 'app.schedule_conds_ext', 'app.schedule_tgapp_ops', 'app.schedule_rules_api', 'app.flow_engine', 'app.flow_adapters', 'app.flow_api', 'app.inbox', 'app.inbox_hooks', 'app.inbox_api', 'app.media_gen', 'app.media_routing', 'app.media_cache', 'app.telegram_media_gen', 'app.telegram_models', 'app.music', 'app.music.api', 'app.music.library', 'app.music.sources', 'app.music.tools', 'app.music.downloader', 'app.music.lyrics', 'app.music.player', 'app.music.tagger', 'app.music.extras', 'app.music.tgapp_ops', 'app.music.telegram_music', 'mutagen', 'mutagen.mp4', 'mutagen.id3', 'mutagen.flac', 'mutagen.oggopus', 'mutagen.oggvorbis', 'app.module_integration', 'app.module_integration_api', 'app.module_integration_handlers', 'app.module_integration_quick', 'app.module_integration_data', 'app.bigdocs', 'app.bigdocs_telegram', 'app.local_hw', 'app.local_wizard', 'app.local_wizard_api', 'app.providers.catalog']
tmp_ret = collect_all('curl_cffi')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pystray')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MemuAIProxy',
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
    version='E:/MemuAI/memuai-proxy-version.txt',
    icon=['E:/MemuAI/icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='MemuAIProxy',
)
