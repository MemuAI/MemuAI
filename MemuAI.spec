# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('app/pgstatic', 'app/pgstatic'), ('app/module_builtins', 'app/module_builtins'), ('app/docs', 'app/docs'), ('app/companion_kb', 'app/companion_kb'), ('app/tgapp', 'app/tgapp'), ('app/memunote/static/js/slides', 'app/memunote/static/js/slides'), ('app/memunote/static/slides.css', 'app/memunote/static'), ('memuai-icon.png', '.'), ('memuai-icon.ico', '.'), ('icon.png', '.')]
binaries = []
hiddenimports = ['uvicorn.loops.auto', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on', 'websockets', 'pystray', 'pypdf', 'winotify', 'PIL.Image', 'PIL.ImageDraw', 'PIL.ImageOps', 'app.api_budget', 'app.model_refresh', 'app.browser_extension', 'app.browser_extension_chat', 'app.browser_extension_files', 'app.browser_extension_store', 'app.browser_extension_connect', 'app.browser_extension_preferences', 'app.browser_extension_tools', 'app.telegram_use', 'app.telegram_use_arguments', 'app.telegram_use_voice', 'app.voice_downloads', 'app.stt_providers', 'app.panels_proxy_usage', 'app.win_window_theme', 'app.cli.file_cache', 'PIL.ImageGrab', 'tkinter', 'clr', 'app.tts_worker', 'app.tg_webapp', 'app.win_titlebar', 'app.win_app_identity', 'app.win_com', 'app.assistant_icon', 'app.win_backdrop', 'app.webview_flags', 'app.chats_delta', 'app.fsindex_ops', 'app.poll_etag', 'app.quick_warm', 'app.appearance_extra', 'app.theme_editor', 'app.ask_user', 'app.ask_user_telegram', 'app.mentions', 'app.canvas', 'app.canvas_files', 'app.live_ws', 'app.quick_ask', 'app.quick_ask_api', 'app.quick_ask_core', 'app.quick_ask_browser', 'app.quick_ask_endpoint', 'app.quick_models', 'app.cu_grounding', 'app.win_hotkey', 'app.panels', 'app.panels_api', 'app.panels_curate', 'app.panels_usage', 'app.panels_system', 'app.panels_options', 'app.panels_local', 'app.panels_layout', 'psutil', 'app.product_cards', 'app.telegram_runtime', 'app.telegram_panels', 'app.dfy', 'app.dfy_api', 'app.dfy_vault', 'app.dfy_browser', 'app.dfy_agent', 'app.dfy_remote', 'app.dfy_remote_api', 'app.dfy_machine', 'app.dfy_proxy', 'app.dfy_takeover', 'app.dfy_remote_browser', 'app.assistant_commands', 'app.assistant_catalog', 'app.assistant_intents', 'app.assistant_actions_win', 'app.assistant_apps', 'app.assistant_layout', 'app.assistant_router', 'app.assistant_procs_win', 'app.reasoning_levels', 'app.reasoning_levels_api', 'app.quota_planner', 'app.composite_models', 'app.composite_models_api', 'app.stream_recovery', 'app.difficulty', 'app.prompt_cache', 'app.web_models', 'app.web_models_api', 'app.model_names', 'app.model_catalog', 'app.model_catalog_api', 'app.assistant_timers', 'app.assistant_r24', 'app.youtube_analysis', 'app.bigdocs', 'app.bigdocs_api', 'app.bigdocs_telegram', 'app.assistant_r25', 'app.tts_providers', 'app.wake_word', 'app.wake_word_api', 'app.wake_audio', 'app.quick_features', 'app.quick_features_win', 'app.telegram_media', 'app.quick_canvas', 'app.quick_actions', 'app.quick_app_icon', 'app.quick_layer', 'app.quick_layer_core', 'app.quick_page_read', 'app.quick_explorer', 'app.quick_apps', 'app.quick_apps_win', 'app.quick_extras', 'app.fsindex', 'app.fsindex_query', 'app.fsindex_win', 'app.fsindex_api', 'app.memory_v2', 'app.memory_api', 'app.search_index', 'app.search_api', 'app.chat_tree', 'app.media_routing', 'app.media_cache', 'app.media_gen', 'app.telegram_media_gen', 'app.telegram_models', 'app.music', 'app.music.api', 'app.music.library', 'app.music.sources', 'app.music.tools', 'app.music.downloader', 'app.music.lyrics', 'app.music.player', 'app.music.tagger', 'app.music.extras', 'app.music.tgapp_ops', 'app.music.telegram_music', 'mutagen', 'mutagen.mp4', 'mutagen.id3', 'mutagen.flac', 'mutagen.oggopus', 'mutagen.oggvorbis', 'app.module_integration', 'app.module_integration_api', 'app.module_integration_handlers', 'app.module_integration_quick', 'app.module_integration_data', 'app.gdrive_sync', 'app.gdrive_sync_crypto', 'app.gdrive_sync_drive', 'app.google_account', 'app.integrations_api', 'app.integrations_gmail', 'app.integrations_notion', 'app.integrations_github', 'app.integrations_drive', 'app.integrations_gcal', 'app.integrations_gworkspace', 'app.integrations_notion_tools', 'app.oauth_config', 'app.oauth_build', 'app.quick_integrations', 'app.gdrive_sync_api', 'app.safe_mode', 'app.docs_ai', 'app.gdrive_picker', 'app.scheduled_messages', 'app.scheduled_messages_api', 'app.scheduled_messages_runner', 'app.schedule_rules_api', 'app.schedule_quick', 'app.schedule_rules', 'app.schedule_runtime', 'app.schedule_sampler', 'app.schedule_triggers_ext', 'app.schedule_conds_ext', 'app.schedule_tgapp_ops', 'app.flow_engine', 'app.flow_adapters', 'app.flow_api', 'app.inbox', 'app.inbox_hooks', 'app.inbox_api', 'app.deeplinks', 'app.deeplinks_routes', 'app.deeplinks_api', 'app.deeplinks_win', 'app.chat_export', 'app.chat_export_api', 'app.chat_export_pdf', 'app.modules_ai_pack', 'app.modules_validate', 'app.tables_api', 'app.skills_catalog', 'app.characters_api', 'app.character_import', 'app.module_apps', 'app.module_apps_win', 'app.stuck_detector', 'app.stuck_api', 'app.answer_guard', 'app.memunote_manager', 'app.memunote', 'app.memunote.winsys', 'app.memunote.paths', 'app.memufiles_manager', 'app.memufiles', 'app.memufiles.desktop', 'app.memufiles.paths', 'app.app_settings_api', 'app.memunote.settings', 'app.memunote.settings_schema', 'app.memunote.desktop', 'app.memunote.ai', 'app.memunote.formats', 'app.memunote.formats.textio', 'app.memufiles.settings', 'app.memufiles.settings_schema']
tmp_ret = collect_all('curl_cffi')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('faster_whisper')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('piper')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('onnxruntime')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pystray')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('vosk')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('telethon')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('python_socks')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('qrcode')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['memuai.py'],
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
    append_pkg=False,
    name='MemuAI',
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
    version='E:/MemuAI/memuai-version.txt',
    icon=['E:/MemuAI/memuai-icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='MemuAI',
)
