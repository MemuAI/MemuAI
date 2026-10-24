"""Сборка MemuAI: exe через PyInstaller и собственный установщик (``installer/``).

Почему это быстрее прежних .bat:
  * программы (MemuAI, MemuAI Proxy и консольный MemuCode CLI) собираются
    параллельно — у каждой своя рабочая папка, они друг другу не мешают;
  * кэш анализа PyInstaller больше не сбрасывается каждый раз (``--clean``
    только по флагу), поэтому повторная сборка не разбирает заново
    onnxruntime, faster-whisper и curl_cffi;
  * ``pip install`` запускается только если изменился requirements.txt;
  * иконка пересобирается только если изменились исходники;
  * программа, у которой не изменился ни один исходник, не пересобирается;
  * установщик (``installer/build_setup.py``) сжимает блоки xz параллельно,
    а ``--fast`` включает быстрое сжатие для тестовых установщиков.

Версия берётся из одного места — ``app/version.py``. Отсюда же
генерируются ресурсы версии для exe и имя/версия установщика
(``installer/MemuAI-Setup-<версия>.exe``, ``installer/MemuAIProxy-Setup-<версия>.exe``).

Запуск: ``python build.py [exe|installer|proxy-installer] [--clean] [--fast]
[--force] [--only memuai|proxy|memucode|memunote|memufiles|proxy_only] [--skip-exe] [--jobs N]``.

Round 40: в конце печатается «Время по этапам», а у каждого exe — фазы PyInstaller (анализ импортов, хуки,
PYZ, EXE, COLLECT; полный журнал — build/<программа>.log). Пропуск неизменившейся программы теперь смотрит
только на ЕЁ файлы (target_inputs: граф импортов tools/import_graph.py + её данные).
"""
from __future__ import annotations

import argparse
import datetime
from functools import lru_cache
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
_BUILD_PYTHON = os.environ.get("MEMUAI_BUILD_PYTHON", "")
VENV_PY = (Path(_BUILD_PYTHON).resolve() if _BUILD_PYTHON else
           VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
STAMP_DIR = ROOT / "build" / ".stamps"

COMMON_HIDDEN = [
    "uvicorn.loops.auto", "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on", "websockets", "pystray", "pypdf", "winotify",
    "PIL.Image", "PIL.ImageDraw", "PIL.ImageOps", "app.api_budget", "app.model_refresh",
    "app.browser_extension", "app.browser_extension_chat", "app.browser_extension_files",
    "app.browser_extension_store", "app.browser_extension_connect", "app.browser_extension_preferences", "app.browser_extension_tools",
    "app.telegram_use", "app.telegram_use_arguments", "app.telegram_use_voice",
    "app.voice_downloads", "app.stt_providers", "app.panels_proxy_usage", "app.win_window_theme",
    "app.cli.file_cache",
]

TARGETS = {
    "proxy": {
        "name": "MemuAIProxy",
        "entry": "main.py",
        "icon": "icon.ico",
        "version_file": "memuai-proxy-version.txt",
        "description": "MemuAI Proxy - local gateway for AI provider accounts",
        "product": "MemuAI Proxy",
        "comments": "Local OpenAI-compatible gateway that balances requests across your own AI provider accounts. Listens on 127.0.0.1 only.",
        "data": ["app/static;app/static", "app/pgstatic;app/pgstatic", "app/companion_kb;app/companion_kb",
                 # Round 33: the local model wizard catalogue (app/local_wizard.py).
                 "app/local_catalog;app/local_catalog",
                 # The background worker reads module manifests independently
                 # of the desktop client's process and install directory.
                 "app/module_builtins;app/module_builtins",
                 # Round 20: the Telegram Mini App runs inside the Proxy (app/tg_webapp.py).
                 "app/tgapp;app/tgapp",
                 "icon.png;.", "icon.ico;."],
        "hidden": COMMON_HIDDEN + ["uvicorn.protocols.http.h11_impl", "uvicorn.lifespan.off",
                                   # Round 33 (r33-thinking): per-model reasoning levels (lazy imports too).
                                   "app.reasoning_levels", "app.reasoning_levels_api", "app.quota_planner", "app.composite_models", "app.composite_models_api", "app.stream_recovery", "app.difficulty", "app.prompt_cache", "app.web_models", "app.web_models_api",
                                   # Round 33 (r33-models): model names + the one catalogue (lazy imports).
                                   "app.model_names", "app.model_catalog", "app.model_catalog_api",
                                   "app.tg_webapp", "app.win_app_identity", "app.win_com", "app.tgapp_api", "app.tgapp_chat", "app.tgapp_code",
                                   "app.cli.remote", "app.cli.session", "app.cli.paths",
                                   # Round 24: /panels in the bot + Mini App «Панели» (lazy imports)
                                   "app.telegram_panels", "app.panels", "app.panels_local", "app.panels_options", "app.panels_layout", "app.theme_editor", "app.ask_user", "app.ask_user_telegram", "app.mentions",
                                   "app.dfy", "app.dfy_vault", "app.stuck_detector",
                                   # Round 33 (r33-chat): hides tool-call markup in Telegram / agents (lazy import).
                                   "app.answer_guard",
                                   # Round 30: DFYManager builds its proxy store; machine tasks (lazy imports)
                                   "app.dfy_proxy", "app.dfy_agent", "app.dfy_remote", "app.dfy_machine",
                                   # Round 25: Telegram voice replies synthesize through it (lazy import)
                                   "app.tts_providers",
                                   # Round 29: memory v2 (Telegram bot / Mini App / admin routes, lazy imports)
                                   "app.memory_v2",
                                   # Round 29: «Синхронизация» — the Proxy keeps backing up from the tray;
                                   # safe mode pauses module jobs while MemuAI runs in safe mode.
                                   "app.gdrive_sync", "app.gdrive_sync_crypto", "app.gdrive_sync_drive",
                                   # Round 30: panels read Calendar through the one Google account; Notion; built-in OAuth clients.
                                   "app.google_account", "app.integrations_gmail", "app.integrations_notion", "app.integrations_notion_tools", "app.oauth_config", "app.oauth_build",
                                   "app.safe_mode",
                                   # Round 29: Mini App — MemuCode on the phone, QR sign-in (/web), voice (lazy imports)
                                   "app.tgapp_code_api", "app.tgapp_extras", "app.tgapp_weblogin", "app.tgapp_complete",
                                   "app.telegram_voice",
                                   # Round 29: global search inside the Mini App (chats.search, search.preview)
                                   "app.search_index", "app.search_api", "app.chat_tree",
                                   # Round 29: scheduled messages are sent from the tray when MemuAI is closed.
                                   "app.scheduled_messages", "app.scheduled_messages_runner",
                                   # Round 29: programmable schedules (rule engine, condition sampler) — lazy in scheduler.py.
                                   "app.schedule_rules", "app.schedule_runtime", "app.schedule_sampler", "app.schedule_triggers_ext", "app.schedule_conds_ext", "app.schedule_tgapp_ops",
                                   "app.schedule_rules_api",   # Mini App «Планирование» (tgapp_extras: parse/save)
                                   "app.flow_engine", "app.flow_adapters", "app.flow_api",   # Round 35: steps inside a task (flows)
                                   "app.inbox", "app.inbox_hooks", "app.inbox_api",   # Round 35: «Входящие» (lazy: scheduler, pool, modules)
                                   # Round 29: image/video generation for Custom connections (lazy import in admin_api);
                                   # r29c-media: the bot / Mini App generation (lazy imports in telegram_bot, tgapp_chat).
                                   "app.media_gen", "app.media_routing", "app.media_cache", "app.telegram_media_gen", "app.telegram_models",
                                   # Round 37: MemuMusic backend (app/music: library, downloads, tags, lyrics, player, Mini App ops).
                                   "app.music", "app.music.api", "app.music.library", "app.music.sources", "app.music.tools", "app.music.downloader", "app.music.lyrics", "app.music.player", "app.music.tagger", "app.music.extras", "app.music.tgapp_ops", "app.music.telegram_music", "mutagen", "mutagen.mp4", "mutagen.id3", "mutagen.flac", "mutagen.oggopus", "mutagen.oggvorbis",
                                   # Round 38: module integration (panels, commands, widgets; Quick Access plugin is imported by name).
                                   "app.module_integration", "app.module_integration_api", "app.module_integration_handlers", "app.module_integration_quick", "app.module_integration_data",
                                   "app.bigdocs", "app.bigdocs_telegram",   # Round 35: big documents in the Telegram bot (lazy import in telegram_bot)
                                   # Round 33: local model wizard (registered in create_app; data in app/local_catalog).
                                   "app.local_hw", "app.local_wizard", "app.local_wizard_api",
                                   # Round 33: provider catalogue (lazy import in admin_api presets).
                                   "app.providers.catalog"],
        "collect": ["curl_cffi", "pystray"],
    },
    "memuai": {
        "name": "MemuAI",
        "entry": "memuai.py",
        "external_pkg": True,
        "icon": "memuai-icon.ico",
        "version_file": "memuai-version.txt",
        "description": "MemuAI Desktop - AI chat, code and agents workspace",
        "product": "MemuAI Desktop",
        "comments": "Desktop client for working with AI models: chat, MemuCode, agents, modules and local models.",
        "data": ["app/pgstatic;app/pgstatic", "app/module_builtins;app/module_builtins",
                 "app/docs;app/docs", "app/companion_kb;app/companion_kb", "app/tgapp;app/tgapp",
                 # r40-canvas: the MemuNote slides engine for «slides» items of the Canvas (/api/pg/canvas-engine/slides/*)
                 "app/memunote/static/js/slides;app/memunote/static/js/slides", "app/memunote/static/slides.css;app/memunote/static",
                 "memuai-icon.png;.", "memuai-icon.ico;.", "icon.png;."],
        "hidden": COMMON_HIDDEN + ["PIL.ImageGrab", "tkinter", "clr", "app.tts_worker", "app.tg_webapp", "app.win_titlebar", "app.win_app_identity", "app.win_com", "app.assistant_icon", "app.win_backdrop", "app.webview_flags", "app.chats_delta", "app.fsindex_ops", "app.poll_etag", "app.quick_warm", "app.appearance_extra", "app.theme_editor", "app.ask_user", "app.ask_user_telegram", "app.mentions", "app.canvas", "app.canvas_files", "app.live_ws",
                                   "app.quick_ask", "app.quick_ask_api", "app.quick_ask_core", "app.quick_ask_browser", "app.quick_ask_endpoint", "app.quick_models", "app.cu_grounding", "app.win_hotkey",
                                   "app.panels", "app.panels_api", "app.panels_curate", "app.panels_usage", "app.panels_system",
                                   "app.panels_options", "app.panels_local", "app.panels_layout",
                                   "psutil", "app.product_cards", "app.telegram_runtime", "app.telegram_panels",
                                   "app.dfy", "app.dfy_api", "app.dfy_vault", "app.dfy_browser",
                                   # Round 30: DFY on machines / takeover / proxy (imported inside functions)
                                   "app.dfy_agent", "app.dfy_remote", "app.dfy_remote_api", "app.dfy_machine",
                                   "app.dfy_proxy", "app.dfy_takeover", "app.dfy_remote_browser",
                                   "app.assistant_commands", "app.assistant_catalog", "app.assistant_intents",
                                   "app.assistant_actions_win", "app.assistant_apps", "app.assistant_layout",
                                   # Round 33: Quick Access AI command router + tray-process helpers (lazy imports).
                                   "app.assistant_router", "app.assistant_procs_win",
                                   # Round 33 (r33-thinking): per-model reasoning levels.
                                   "app.reasoning_levels", "app.reasoning_levels_api", "app.quota_planner", "app.composite_models", "app.composite_models_api", "app.stream_recovery", "app.difficulty", "app.prompt_cache", "app.web_models", "app.web_models_api",
                                   # Round 33 (r33-models): model names + the one catalogue (lazy imports).
                                   "app.model_names", "app.model_catalog", "app.model_catalog_api",
                                   "app.assistant_timers", "app.assistant_r24", "app.youtube_analysis", "app.bigdocs", "app.bigdocs_api", "app.bigdocs_telegram",
                                   # Round 25: imported inside functions (quick commands, voice/TTS).
                                   "app.assistant_r25", "app.tts_providers",
                                   # Round 27: the wake word (a Quick Ask backend plugin, imported by name).
                                   "app.wake_word", "app.wake_word_api", "app.wake_audio",
                                   # Round 27: new Quick Ask features (backend plugin "features").
                                   "app.quick_features", "app.quick_features_win", "app.telegram_media",
                                   # Round 27: panels and quick actions in Quick Ask (backend plugin "canvas").
                                   "app.quick_canvas", "app.quick_actions", "app.quick_app_icon",
                                   "app.quick_layer", "app.quick_layer_core",
                                   # Round 32: fast reading of the browser tab for Quick Ask.
                                   "app.quick_page_read",
                                   # Round 29: Quick Ask over Explorer / other programs + extras (backend plugin "explorer").
                                   "app.quick_explorer", "app.quick_apps", "app.quick_apps_win", "app.quick_extras",
                                   "app.fsindex", "app.fsindex_query", "app.fsindex_win", "app.fsindex_api",
                                   # Round 29: memory v2 and its /api/memory routes.
                                   "app.memory_v2", "app.memory_api",
                                   # Round 29: global search (index + /api/search routes).
                                   "app.search_index", "app.search_api", "app.chat_tree",
                                   # Round 29: text / image / video routing in the chat (MemuAI + Proxy side).
                                   "app.media_routing", "app.media_cache", "app.media_gen", "app.telegram_media_gen", "app.telegram_models",
                                   # Round 37: MemuMusic backend (app/music: library, downloads, tags, lyrics, player, Mini App ops).
                                   "app.music", "app.music.api", "app.music.library", "app.music.sources", "app.music.tools", "app.music.downloader", "app.music.lyrics", "app.music.player", "app.music.tagger", "app.music.extras", "app.music.tgapp_ops", "app.music.telegram_music", "mutagen", "mutagen.mp4", "mutagen.id3", "mutagen.flac", "mutagen.oggopus", "mutagen.oggvorbis",
                                   # Round 38: module integration (panels, commands, widgets; Quick Access plugin is imported by name).
                                   "app.module_integration", "app.module_integration_api", "app.module_integration_handlers", "app.module_integration_quick", "app.module_integration_data",
                                   # Round 29: «Синхронизация» (Google Drive / folder backups) and safe mode.
                                   "app.gdrive_sync", "app.gdrive_sync_crypto", "app.gdrive_sync_drive",
                                   # Round 30: Google (one account) + Notion integrations, Quick Ask plugin, built-in OAuth clients.
                                   "app.google_account", "app.integrations_api", "app.integrations_gmail", "app.integrations_notion",
                                   # Round 33: GitHub / Drive / Calendar / Docs-Sheets-Tasks-Contacts / Notion tool sets.
                                   "app.integrations_github", "app.integrations_drive", "app.integrations_gcal",
                                   "app.integrations_gworkspace", "app.integrations_notion_tools",
                                   "app.oauth_config", "app.oauth_build", "app.quick_integrations",
                                   "app.gdrive_sync_api", "app.safe_mode",
                                   # Round 29: docs «AI summary» and «Add from Google Drive» picker.
                                   "app.docs_ai", "app.gdrive_picker",
                                   # Round 29: scheduled messages and the chat export (PDF / DOCX / HTML).
                                   "app.scheduled_messages", "app.scheduled_messages_api",
                                   "app.scheduled_messages_runner",
                                   # Round 29: programmable schedules — /api/plan/* and the rule engine.
                                   "app.schedule_rules_api", "app.schedule_quick", "app.schedule_rules", "app.schedule_runtime", "app.schedule_sampler", "app.schedule_triggers_ext", "app.schedule_conds_ext", "app.schedule_tgapp_ops",
                                   "app.flow_engine", "app.flow_adapters", "app.flow_api",   # Round 35: steps inside a task (flows)
                                   "app.inbox", "app.inbox_hooks", "app.inbox_api",   # Round 35: «Входящие» hub
                                   "app.deeplinks", "app.deeplinks_routes", "app.deeplinks_api", "app.deeplinks_win",   # Round 35: memuai:// links
                                   "app.chat_export", "app.chat_export_api", "app.chat_export_pdf",
                                   # Round 29: modules AI pack + package validation (/api/pg/modules-ai/*).
                                   "app.modules_ai_pack", "app.modules_validate",
                                   # Round 29: table editor .xlsx import (/api/tables/xlsx).
                                   "app.tables_api",
                                   # Round 32: skill catalog + Agent Skills format (/api/pg/skills-catalog/*).
                                   "app.skills_catalog",
                                   # Round 29: Memu.complete / Memu.store / Memu.asset (Characters module).
                                   "app.characters_api", "app.character_import",
                                   # Round 33: modules as their own windows / installed apps (MemuAI.exe --module <id>).
                                   "app.module_apps", "app.module_apps_win",
                                   # Round 29: stuck detector (pause for the user's verdict) + /api/stuck routes.
                                   "app.stuck_detector", "app.stuck_api",
                                   # Round 33 (r33-chat): hides tool-call markup in Quick Access / agents (lazy import).
                                   "app.answer_guard",
                                   # Round 29: Settings → «MemuNote» (install / open / remove the AI notepad).
                                   "app.memunote_manager", "app.memunote", "app.memunote.winsys",
                                   "app.memunote.paths",
                                   # Round 35: Settings → «MemuFiles» + «Open in MemuFiles» (Quick Access file search).
                                   "app.memufiles_manager", "app.memufiles", "app.memufiles.desktop", "app.memufiles.paths",
                                   # Round 38: every MemuNote / MemuFiles preference in Settings (schemas + their settings stores).
                                   "app.app_settings_api", "app.memunote.settings", "app.memunote.settings_schema", "app.memunote.desktop",
                                   "app.memunote.ai", "app.memunote.formats", "app.memunote.formats.textio",
                                   "app.memufiles.settings", "app.memufiles.settings_schema"],
        "collect": ["curl_cffi", "faster_whisper", "piper", "onnxruntime", "pystray", "vosk",
                    "telethon", "python_socks", "qrcode"],
    },
    "memucode": {
        "name": "MemuCode",
        "entry": "memucode.py",
        "icon": "memuai-icon.ico",
        "version_file": "memucode-version.txt",
        "description": "MemuCode CLI - the MemuAI coding agent for the terminal",
        "product": "MemuCode CLI",
        "comments": "Terminal coding agent of MemuAI: edits code, runs commands in a sandbox, uses the models of MemuAI Proxy.",
        "console": True,
        "data": ["app/docs;app/docs"],
        "hidden": ["websockets", "pypdf", "winotify", "PIL.Image", "PIL.ImageGrab", "PIL.ImageDraw", "PIL.ImageFont",
                   # Round 15: pictures for models without eyes (/vision, the probe) and instant answers of web search.
                   "app.cli.vision", "app.instant_answers",
                   "app.cli.commands.config_cmds", "app.cli.commands.info_cmds", "app.cli.commands.model_cmds",
                   "app.cli.commands.session_cmds", "app.cli.commands.work_cmds",
                   # Round 14: loaded inside functions (the command registry, /schedule, /resume-*), so named here
                   # rather than left to the import scan.
                   "app.cli.commands.dashboard_cmds", "app.cli.commands.decisions_cmds",
                   "app.cli.commands.foreign_cmds", "app.cli.commands.import_cmds", "app.cli.commands.routing_cmds",
                   "app.cli.commands.schedule_cmds", "app.cli.commands.skill_cmds", "app.cli.foreign",
                   "app.cli.foreign.agy", "app.cli.foreign.claude", "app.cli.foreign.codex", "app.cli.foreign.configs",
                   "app.cli.foreign.cursor", "app.cli.foreign.gemini", "app.cli.foreign.grok", "app.cli.foreign.handoff",
                   "app.cli.foreign.importer", "app.cli.foreign.model", "app.cli.foreign.opencode",
                   "app.cli.foreign.protobuf", "app.cli.schedule", "app.cli.triage", "app.cli.risk", "app.cli.modelmeta",
                   "app.cli.skillkit", "app.cli.editor", "app.cli.tools.skills", "sqlite3", "tomllib",
                   # Round 20: a running terminal publishes itself to the Telegram Mini App.
                   "app.cli.remote",
                   # Round 29: a terminal hosted for the Mini App (MemuCode on the phone, lazy import).
                   "app.cli.remote_host",
                   # Round 29: MemuAI memory in the CLI prompt and /remember.
                   "app.memory_v2",
                   # Round 29: the stuck detector of the agent loop (lazy import in app/cli/agent.py).
                   "app.stuck_detector",
                   # Round 33 (r33-chat): answer cleaner behind clean_ai_text (lazy import).
                   "app.answer_guard",
                   # Round 32: open classification of model errors (lazy import in app/cli/agent.py).
                   "app.cli.errors",
                   # Round 33: /auto-continue, limit reset times, the built-in MemuCode guide (lazy imports).
                   "app.cli.autocontinue", "app.cli.ratelimit", "app.cli.selfdoc",
                   "app.cli.commands.autocontinue_cmds", "app.cli.commands.guide_cmds",
                   # Round 33 (r33c-models-lists): readable model names in /model (lazy imports in model_cmds, tui.input).
                   "app.model_names",
                   # r33-thinking: /effort lists the levels of the current model (lazy import in model_cmds).
                   "app.reasoning_levels"],
        "collect": ["prompt_toolkit", "rich", "pygments", "anthropic", "curl_cffi", "wcwidth"],
        # Local speech and model runtimes stay in MemuAI: the CLI transcribes
        # through the running app, so they would only triple the download.
        "exclude": ["faster_whisper", "ctranslate2", "av", "onnxruntime", "tokenizers", "huggingface_hub",
                    "hf_xet", "numpy", "piper", "clr", "pythonnet", "clr_loader", "prompt_toolkit.contrib.ssh"],
    },
    # Round 35 (protocol): memu — the MemuAI command line (memu.exe). Standard library only, so it stays small;
    # build_exes() copies dist/memu into dist/MemuAI/MemuCLI (installed with MemuAI, MemuAI\MemuCLI in PATH).
    "memu": {
        "name": "memu",
        "entry": "memu.py",
        "icon": "memuai-icon.ico",
        "version_file": "memu-version.txt",
        "description": "memu - the MemuAI command line",
        "product": "MemuAI command line",
        "comments": "Opens chats, links and tasks in the running MemuAI and asks models through MemuAI Proxy from a terminal.",
        "console": True,
        "data": [],
        "hidden": ["app.memu_cli", "app.deeplinks", "app.deeplinks_routes", "app.version"],
        "collect": [],
        "exclude": ["fastapi", "uvicorn", "starlette", "pydantic", "httpx", "curl_cffi", "PIL", "numpy", "tkinter",
                    "app.config", "app.pgdata", "app.providers", "app.skills_catalog", "app.memunote_manager",
                    "app.fsindex_api", "faster_whisper", "onnxruntime", "piper", "vosk", "pystray", "webview", "clr",
                    # memu serializes protocol routes and forwards them to the desktop.
                    # It never executes the server-only browser pairing handler.
                    "app.browser_extension_connect"],
    },
    # Round 29: MemuNote — the light AI notepad (optional component of the Setup; shipped as
    # Packages\\MemuNote.zip otherwise). A small pywebview app: no FastAPI/uvicorn, no models.
    "memunote": {
        "name": "MemuNote",
        "entry": "memunote.py",
        "icon": "memunote-icon.ico",
        "version_file": "memunote-version.txt",
        "description": "MemuNote - the light AI notepad of MemuAI",
        "product": "MemuNote",
        "comments": "Fast notepad for huge files, Markdown and Word/ODT/RTF documents with AI through MemuAI Proxy.",
        "data": ["app/memunote/static;app/memunote/static", "memunote-icon.png;.",
                 # Round 33: Hunspell dictionaries (ru_RU, en_US) of the built-in spell checker
                 "app/memunote/dicts;app/memunote/dicts"],
        "hidden": ["app.memunote", "app.memunote.server", "app.memunote.desktop", "app.memunote.ai",
                   "app.memunote.prompts", "app.memunote.edits", "app.memunote.settings", "app.memunote.settings_schema", "app.memunote.paths", "app.memunote.winsys",
                   "app.memunote.formats", "app.memunote.formats.textio", "app.memunote.formats.richhtml",
                   "app.memunote.formats.docx", "app.memunote.formats.odt", "app.memunote.formats.rtf",
                   "app.memunote.formats.pdftools", "app.memunote.formats.sheets", "app.memunote.formats.books",
                   # Round 30: the crash log / message box (imported first by memunote.py).
                   "app.memunote.crash", "app.memory_v2",
                   # Round 32 (slides): AI routes of the presentation editor.
                   "app.memunote.slides_api", "app.storage", "app.themes", "app.version", "clr", "pypdf",
                   # Round 32 (photo): photo editor routes, image codecs (Pillow) and the PSD reader/writer.
                   "app.memunote.photo", "app.memunote.formats.images", "app.memunote.formats.psd", "PIL", "PIL.Image",
                   # Round 33: run files, spell checking (spylls = pure-Python Hunspell; Windows fallback app.spellcheck).
                   "app.memunote.runner", "app.memunote.spell", "app.spellcheck",
                   # Round 33 (r33c-models-lists): readable model names in the AI model menu (lazy import in ai.py).
                   "app.model_names",
                   # Round 35 (bigdocs): documents of any size in the AI panel (lazy import in server.py).
                   "app.memunote.bigdocs_api", "app.bigdocs",
                   # Round 40: memory_v2 / bigdocs estimate tokens with it (lazy imports).
                   "app.token_estimate",
                   # Round 40 (r40-note): editable .xlsx (cell patcher + save route, lazy imports).
                   "app.memunote.sheets_api", "app.memunote.formats.xlsx_edit"],
        "collect": ["spylls"],
        # The Proxy does web search; the local fallback (curl_cffi, providers) would triple the size.
        # app.pgdata (MemuAI's chat data) imports app.config, and through lazy imports half of app.*:
        # MemuNote reaches it only behind try/except (memory_v2.memu_dir, themes_dir), so it stays out.
        # tests/round30_memunote_test.py checks that MemuNote never imports an excluded module unguarded.
        # Round 40 (п.9): app.inbox_hooks — memory_v2's guarded «Входящие» sync (it needs app.config, excluded
        # anyway). PyInstaller follows lazy imports too: through it MemuNote's analysis reached 390 app modules,
        # anthropic (~2200 modules), pygments, rich, prompt_toolkit, mutagen… — most of its 208 s build.
        # httpx._main — the httpx command line (click + rich + pygments with ~340 lexer modules); httpx/__init__
        # imports it in try/except ImportError. tools/import_graph.py shows what a target reaches;
        # tests/round40_build_icons_test.py keeps it light.
        "exclude": ["app.websearch", "app.config", "app.pgdata", "app.providers", "app.inbox_hooks", "httpx._main", "curl_cffi", "fastapi", "uvicorn", "starlette",
                    "pydantic", "faster_whisper", "ctranslate2", "av", "onnxruntime", "tokenizers",
                    "huggingface_hub", "hf_xet", "numpy", "piper", "vosk", "tkinter", "pystray"],
    },
    # Round 35: MemuFiles — the fast file manager (optional component of the Setup like MemuNote;
    # Packages\\MemuFiles.zip otherwise). Stdlib HTTP server + pywebview; reuses MemuNote's window
    # helpers and AI client and the PC-wide file index (app.fsindex) for instant search.
    "memufiles": {
        "name": "MemuFiles",
        "entry": "memufiles.py",
        "icon": "memufiles-icon.ico",
        "version_file": "memufiles-version.txt",
        "description": "MemuFiles - the fast file manager of MemuAI",
        "product": "MemuFiles",
        "comments": "Fast file manager with tabs, two panes, instant search, previews and an AI assistant through MemuAI Proxy.",
        "data": ["app/memufiles/static;app/memufiles/static", "memufiles-icon.png;."],
        "hidden": ["app.memufiles", "app.memufiles.server", "app.memufiles.desktop", "app.memufiles.crash",
                   "app.memufiles.paths", "app.memufiles.settings", "app.memufiles.settings_schema", "app.memufiles.fsops", "app.memufiles.trash",
                   "app.memufiles.opqueue", "app.memufiles.batchrename", "app.memufiles.search", "app.memufiles.jobs",
                   "app.memufiles.preview", "app.memufiles.assistant", "app.memufiles.textedit", "app.memufiles.clip",
                   "app.memufiles.openwith", "app.win_titlebar",    # Round 36: Open with, custom title bar
                   "app.memufiles.extras",                          # Round 39: compare, templates, recent… (lazy import)
                   "app.memufiles.organize", "app.memufiles.feat40",  # Round 40 (r40-files-max): AI sort by meaning, Properties → Details (lazy)
                   "app.memufiles.taskbar",                         # Round 40 (r40-files-core): taskbar progress (lazy)
                   "app.memufiles.archive", "app.memufiles.archive_assoc",  # Round 41 (r41-files): archives
                   # MemuNote's window/instance helpers and the Proxy client (lazy imports in desktop/server/assistant).
                   "app.memunote", "app.memunote.ai", "app.memunote.paths", "app.memunote.desktop",
                   "app.memunote.winsys", "app.memunote.crash", "app.model_names", "app.memory_v2", "app.storage",
                   "app.token_estimate",
                   # Instant search: the PC-wide index (Everything / NTFS MFT / own walker), lazy in search.py.
                   "app.fsindex", "app.fsindex_query", "app.fsindex_win",
                   "app.themes", "app.version", "clr", "PIL", "PIL.Image"],
        "collect": [],
        # Same diet as MemuNote: no FastAPI/uvicorn, no providers, no speech/ML stacks.
        "exclude": ["app.websearch", "app.config", "app.pgdata", "app.providers", "app.quick_files", "app.quick_apps",
                    "app.inbox_hooks",   # Round 40: see MemuNote — the lazy import that pulled in half of app.*
                    "curl_cffi", "fastapi", "uvicorn", "starlette", "pydantic", "httpx", "faster_whisper", "ctranslate2",
                    "av", "onnxruntime", "tokenizers", "huggingface_hub", "hf_xet", "numpy", "piper", "vosk",
                    "tkinter", "pystray", "spylls"],
    },
    "proxy_only": {
        "name": "MemuAIProxy",
        "entry": "proxy_only.py",
        "icon": "icon.ico",
        "version_file": "memuai-proxy-version.txt",
        "description": "MemuAI Proxy - local gateway for AI provider accounts",
        "product": "MemuAI Proxy",
        "comments": "Local OpenAI-compatible gateway that balances requests across your own AI provider accounts. Listens on 127.0.0.1 only.",
        "data": ["build/proxy-only-static;app/static", "build/proxy-only-kb;app/companion_kb",
                 "app/local_catalog;app/local_catalog",   # Round 33: local model wizard catalogue
                 "app/module_builtins;app/module_builtins", "icon.png;.", "icon.ico;."],
        "hidden": COMMON_HIDDEN + ["uvicorn.protocols.http.h11_impl", "uvicorn.lifespan.off",
                                   "app.win_app_identity", "app.win_com",
                                   # Round 29: scheduled messages sent by the Proxy core (lazy import in app/main.py).
                                   "app.scheduled_messages", "app.scheduled_messages_runner",
                                   # Round 29: programmable schedules (rule engine, condition sampler) — lazy in scheduler.py.
                                   "app.schedule_rules", "app.schedule_runtime", "app.schedule_sampler", "app.schedule_triggers_ext", "app.schedule_conds_ext", "app.schedule_tgapp_ops",
                                   "app.schedule_rules_api",   # Mini App «Планирование» (tgapp_extras: parse/save)
                                   "app.flow_engine", "app.flow_adapters", "app.flow_api",   # Round 35: steps inside a task (flows)
                                   "app.inbox", "app.inbox_hooks", "app.inbox_api",   # Round 35: «Входящие» (lazy: scheduler, pool, modules)
                                   # Round 29: image/video generation for Custom connections (lazy import in admin_api);
                                   # r29c-media: the bot / Mini App generation (lazy imports in telegram_bot, tgapp_chat).
                                   "app.media_gen", "app.media_routing", "app.media_cache", "app.telegram_media_gen", "app.telegram_models",
                                   "app.bigdocs", "app.bigdocs_telegram",   # Round 35: big documents in the Telegram bot (lazy import in telegram_bot)
                                   # Round 33: local model wizard (registered in create_app; data in app/local_catalog).
                                   "app.local_hw", "app.local_wizard", "app.local_wizard_api",
                                   # Round 35: composite models (registered in create_app; lazy in chat/openai_api).
                                   "app.composite_models", "app.composite_models_api", "app.stream_recovery", "app.difficulty", "app.prompt_cache", "app.web_models", "app.web_models_api",
                                   # Round 33: provider catalogue (lazy import in admin_api presets).
                                   "app.providers.catalog"],
        "collect": ["curl_cffi", "pystray"],
        "dist": "dist/proxy-only/MemuAIProxy",
        "dist_root": "dist/proxy-only",
        "work": "build/proxy-only-pyinstaller",
        "spec": "build/proxy-only-spec",
    },
}

# Что влияет на результат сборки (для пропуска неизменившейся программы).
INPUT_GLOBS = ["*.py", "app/**/*", "requirements.txt", "*.ico", "*.png", "build.py"]
# Round 40: generated data folders → the sources they are made from (prepare_proxy_only_assets).
DATA_SOURCES = {"build/proxy-only-static": "app/static", "build/proxy-only-kb": "app/companion_kb"}


def prepare_proxy_only_assets() -> None:
    """Create a Proxy UI and help corpus with no chat-app references."""
    build_root = (ROOT / "build").resolve()
    for source_name, output_name in (("app/static", "proxy-only-static"),
                                     ("app/companion_kb", "proxy-only-kb")):
        output = build_root / output_name
        if output.resolve().parent != build_root:
            raise SystemExit("Некорректный путь временных файлов Proxy")
        if output.exists():
            shutil.rmtree(output)
        shutil.copytree(ROOT / source_name, output)

    ui = build_root / "proxy-only-static"
    kb = build_root / "proxy-only-kb"

    def replace_exact(path: Path, old: str, new: str, count: int = 1) -> None:
        value = path.read_text(encoding="utf-8")
        if value.count(old) != count:
            raise SystemExit(f"Proxy-only UI changed unexpectedly: {path.name}: {old[:50]}")
        path.write_text(value.replace(old, new), encoding="utf-8")

    replace_exact(ui / "index.html",
                  '            <button class="btn" id="open-memuai" data-i18n="home.openchat">✦ Открыть MemuAI</button>\n', "")
    app_js = ui / "app.js"
    value = app_js.read_text(encoding="utf-8")
    value, removed = re.subn(r'\n\$\("#open-memuai"\)\.onclick = async \(\) => \{.*?\n\};\n',
                             "\n", value, count=1, flags=re.S)
    if removed != 1:
        raise SystemExit("Proxy-only UI: open chat handler not found")
    app_js.write_text(value, encoding="utf-8")
    for name in ("accounts.json", "models.json", "logs.json", "logs.txt"):
        replace_exact(app_js, f'"memuai-{name}"', f'"memuai-proxy-{name}"')
    i18n = ui / "i18n.js"
    value = i18n.read_text(encoding="utf-8")
    value, removed = re.subn(r'^\s*"home\.openchat(?:\.done)?":.*\n', "", value, flags=re.M)
    if removed != 4:
        raise SystemExit("Proxy-only UI: chat translations changed")
    i18n.write_text(value, encoding="utf-8")
    replace_exact(ui / "local.js", "Скопировать в папку MemuAI", "Скопировать в папку Proxy")
    replace_exact(ui / "local.js", "Copy into the MemuAI folder", "Copy into the Proxy folder")

    changes = {
        "api.md": [("расширения MemuAI", "расширения Proxy")],
        "local.md": [(r"%APPDATA%\MemuAI\local-models", r"%APPDATA%\MemuAIProxy\local-models"),
                     ("в папку MemuAI", "в папку Proxy")],
        "overview.md": [(" Чат MemuAI работает через этот же Proxy.", ""),
                        (", «Открыть MemuAI»", ""),
                        (r"%APPDATA%\MemuAI", r"%APPDATA%\MemuAIProxy")],
        "provider-genspark.md": [("из MemuAI", "через API Proxy")],
        "troubleshooting.md": [("- **MemuAI пишет «Proxy не найден / нет связи»** — Proxy не запущен или слушает другой порт. Запустить MemuAI Proxy из меню Пуск или трея; проверить порт в настройках.\n", ""),
                               ("; MemuAI найдёт новый порт сам", "")],
    }
    for name, replacements in changes.items():
        for old, new in replacements:
            replace_exact(kb / name, old, new)
    for folder in (ui, kb):
        for path in folder.rglob("*"):
            if path.suffix not in (".js", ".html", ".md", ".css"):
                continue
            if re.search(r"\bMemuAI\b(?!\s+Proxy)", path.read_text(encoding="utf-8")):
                raise SystemExit(f"Proxy-only asset mentions the chat app: {path}")


def say(text: str) -> None:
    print(text, flush=True)


# Round 40 (п.9): how long each step took — printed at the end, so a slow build says where the time went.
STAGES: list = []
_stage_lock = threading.Lock()


class stage:
    """``with stage("pip"):`` — record the step's wall time (also usable from worker threads)."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self):
        self.started = time.time()
        return self

    def __exit__(self, *exc) -> None:
        with _stage_lock:
            STAGES.append((self.name, time.time() - self.started))


# PyInstaller's INFO lines start with milliseconds since its start: the phases of one exe build.
_PHASES = (("анализ импортов", "INFO: Analyzing "), ("хуки и бинарники", "INFO: Processing module hooks"),
           ("PYZ", "INFO: Building PYZ"), ("EXE", "INFO: Building EXE"), ("COLLECT", "INFO: Building COLLECT"))


def pyinstaller_phases(log_text: str) -> list:
    """``[(phase, seconds)]`` from a PyInstaller log (``--log-level INFO``)."""
    marks = []
    for name, needle in _PHASES:
        match = re.search(r"^(\d+) " + re.escape(needle), log_text, re.M)
        if match:
            marks.append((name, int(match.group(1)) / 1000))
    end = re.findall(r"^(\d+) INFO: ", log_text, re.M)
    if not marks or not end:
        return []
    marks.sort(key=lambda item: item[1])
    stops = [t for _n, t in marks[1:]] + [int(end[-1]) / 1000]
    return [(name, max(0.0, stop - start)) for (name, start), stop in zip(marks, stops)]


def stage_report() -> None:
    if not STAGES:
        return
    say("Время по этапам:")
    for name, seconds in STAGES:
        say(f"  {name:<40} {seconds:7.1f} с")


def app_version() -> str:
    text = (ROOT / "app" / "version.py").read_text(encoding="utf-8")
    match = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', text)
    if not match:
        raise SystemExit("В app/version.py не найдена APP_VERSION")
    return match.group(1)


def target_version(key: str, app_release: str) -> str:
    """Every executable carries the version of the bundle (``app/version.py``) — MemuCode CLI included."""
    return app_release


def version_tuple(version: str) -> tuple:
    parts = [int(p) for p in re.findall(r"\d+", version)][:4]
    return tuple(parts + [0] * (4 - len(parts)))


def write_version_file(target: dict, version: str) -> None:
    numbers = version_tuple(version)
    dotted = ".".join(str(n) for n in numbers)
    exe = target["name"] + ".exe"
    # A complete version resource: legitimate applications carry the full set of
    # fields (company, copyright, description, comments). These identify the
    # product; they do not guarantee that Defender will accept it. Signing may
    # help identify the publisher, and a disputed detection should be submitted
    # to Microsoft for analysis. Neither metadata nor signing prevents all
    # false positives.
    year = datetime.date.today().year
    comments = target.get("comments") or target["description"]
    body = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('Comments', '{comments}'),
      StringStruct('CompanyName', 'MemuAI Project'),
      StringStruct('FileDescription', '{target["description"]}'),
      StringStruct('FileVersion', '{dotted}'),
      StringStruct('InternalName', '{target["name"]}'),
      StringStruct('LegalCopyright', '(c) {year} MemuAI Project. All rights reserved.'),
      StringStruct('OriginalFilename', '{exe}'),
      StringStruct('ProductName', '{target["product"]}'),
      StringStruct('ProductVersion', '{dotted}')
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    path = ROOT / target["version_file"]
    if not path.is_file() or path.read_text(encoding="utf-8") != body:
        path.write_text(body, encoding="utf-8")


def file_hash(paths) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        content = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                content.update(chunk)
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8") + b"\0")
        digest.update(content.digest())
    return digest.hexdigest()


@lru_cache(maxsize=1)
def runtime_fingerprint() -> str:
    """The actual interpreter and distributions used to freeze executables."""
    script = ("import importlib.metadata as m,json,sys; "
              "print(json.dumps({'python':sys.version, 'packages':sorted("
              "(d.metadata['Name'],d.version) for d in m.distributions())},sort_keys=True))")
    return subprocess.check_output([str(VENV_PY), "-c", script], encoding="utf-8").strip()


def _import_graph():
    import importlib.util
    spec = importlib.util.spec_from_file_location("memuai_import_graph", ROOT / "tools" / "import_graph.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def target_inputs(target: dict) -> set:
    """Round 40: the files ONE program is built from — its script, every app module PyInstaller reaches from it
    (tools/import_graph.py walks the same import edges, lazy imports included), its data folders, icon and
    version file. Editing the chat UI no longer rebuilds MemuNote, MemuFiles, MemuCode and memu as well."""
    graph = _import_graph()
    result = graph.analyse(target["entry"], list(target["hidden"]) + list(target.get("collect", [])),
                           target.get("exclude", []))
    files = {ROOT / target["entry"], ROOT / "build.py", ROOT / "build_freeze.py", ROOT / "requirements.txt",
             ROOT / target["icon"], ROOT / "tools" / "import_graph.py"}
    for name in result["app"]:
        path = graph._module_file(name)
        if path is not None:
            files.add(path)
    for item in target["data"]:
        source = item.split(";", 1)[0]
        source = DATA_SOURCES.get(source, source)
        path = ROOT / source
        if path.is_dir():
            files.update(p for p in path.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
        elif path.is_file():
            files.add(path)
    return {path for path in files if path.is_file()}


def inputs_hash(extra: str, target: Optional[dict] = None) -> str:
    """Hash of what a build depends on: ``target``'s own inputs (Round 40), else everything (INPUT_GLOBS)."""
    if target is not None:
        return hashlib.sha256((file_hash(target_inputs(target)) + extra + json.dumps(target, sort_keys=True)
                               + runtime_fingerprint()).encode()).hexdigest()
    files = set()
    for pattern in INPUT_GLOBS:
        for path in ROOT.glob(pattern):
            if path.is_file() and "__pycache__" not in path.parts:
                files.add(path)
    return hashlib.sha256((file_hash(files) + extra + runtime_fingerprint()).encode()).hexdigest()


def read_stamp(name: str) -> str:
    try:
        return (STAMP_DIR / name).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_stamp(name: str, value: str) -> None:
    STAMP_DIR.mkdir(parents=True, exist_ok=True)
    (STAMP_DIR / name).write_text(value, encoding="utf-8")


def run(cmd, **kwargs) -> None:
    result = subprocess.run(cmd, cwd=ROOT, **kwargs)
    if result.returncode:
        raise SystemExit(f"Команда завершилась с ошибкой ({result.returncode}): {cmd[0]}")


def ensure_venv() -> None:
    if not VENV_PY.is_file():
        if _BUILD_PYTHON:
            raise SystemExit(f"Указанный MEMUAI_BUILD_PYTHON не найден: {VENV_PY}")
        say("Создаю виртуальное окружение…")
        run([sys.executable, "-m", "venv", str(VENV)])
    reqs = (ROOT / "requirements.txt").read_bytes()
    def dependency_key() -> str:
        return hashlib.sha256(reqs + b"pyinstaller pillow" + runtime_fingerprint().encode()).hexdigest()
    key = dependency_key()
    if read_stamp("pip") == key:
        say("Зависимости не менялись — pip пропущен.")
        return
    say("Устанавливаю зависимости…")
    run([str(VENV_PY), "-m", "pip", "install", "-q", "--disable-pip-version-check",
         "-r", "requirements.txt", "pyinstaller", "pillow"])
    runtime_fingerprint.cache_clear()
    write_stamp("pip", dependency_key())


def ensure_icon() -> None:
    # Round 33: the icons are committed and their scripts need Playwright Chromium,
    # so they are only rebuilt when a file is missing (never because of an mtime).
    for script_name, outputs in (("build_memuai_icon.py", ("memuai-icon.ico", "memuai-icon.png")),
                                 ("build_proxy_icon.py", ("icon.ico", "icon.png"))):
        script = ROOT / script_name
        if not script.is_file() or all((ROOT / name).is_file() for name in outputs):
            continue
        say(f"Собираю иконку ({script_name})…")
        run([str(VENV_PY), str(script)])


def pyinstaller_cmd(target: dict, clean: bool) -> list:
    workpath = ROOT / target.get("work", "build/" + target["name"])
    specpath = ROOT / target.get("spec", ".")
    distpath = ROOT / target.get("dist_root", "dist")
    specpath.mkdir(parents=True, exist_ok=True)
    cmd = [str(VENV_PY), "-m", "PyInstaller", "--noconfirm", "--noupx",
           "--console" if target.get("console") else "--windowed",
           "--log-level", "INFO",   # Round 40: phase timings in build/<key>.log (pyinstaller_phases)
           "--name", target["name"],
           "--workpath", str(workpath),
           "--specpath", str(specpath),
           "--distpath", str(distpath),
           "--version-file", str(ROOT / target["version_file"]),
           "--icon", str(ROOT / target["icon"])]
    if clean:
        cmd.append("--clean")
    for item in target["data"]:
        if "spec" in target:
            source, destination = item.split(";", 1)
            item = f"{ROOT / source};{destination}"
        cmd += ["--add-data", item]
    for item in target["hidden"]:
        cmd += ["--hidden-import", item]
    for item in target["collect"]:
        cmd += ["--collect-all", item]
    for item in target.get("exclude", ()):
        cmd += ["--exclude-module", item]
    cmd.append(target["entry"])
    return cmd


OAUTH_LOCAL = ROOT / "oauth_clients.local.json"
OAUTH_BUILD = ROOT / "app" / "oauth_build.py"


def write_oauth_build() -> None:
    """Bake private Google, Notion and Antigravity OAuth clients into the build.

    Values come from MEMUAI_GOOGLE_CLIENT_ID / MEMUAI_GOOGLE_CLIENT_SECRET / MEMUAI_NOTION_CLIENT_ID /
    MEMUAI_NOTION_CLIENT_SECRET, MEMUAI_ANTIGRAVITY_CLIENT_ID / MEMUAI_ANTIGRAVITY_CLIENT_SECRET
    or from oauth_clients.local.json next to this file (google / notion / antigravity entries;
    each has client_id and client_secret; keep the file out of version control).
    Without them the file is written empty and users add their own client in Settings → Integrations.
    """
    local = {}
    if OAUTH_LOCAL.is_file():
        try:
            local = json.loads(OAUTH_LOCAL.read_text(encoding="utf-8"))
        except ValueError:
            raise SystemExit(f"{OAUTH_LOCAL.name}: неверный JSON")
    values = {}
    for provider in ("google", "notion", "antigravity"):
        row = local.get(provider) if isinstance(local.get(provider), dict) else {}
        for field in ("client_id", "client_secret"):
            env = f"MEMUAI_{provider.upper()}_{field.upper()}"
            values[env[len("MEMUAI_"):]] = os.environ.get(env, "").strip() or str(row.get(field) or "").strip()
    body = '"""Written by build.py (Round 30): OAuth clients baked into this build. See app/oauth_config.py."""\n' + \
        "".join(f"{name} = {value!r}\n" for name, value in values.items())
    if not OAUTH_BUILD.is_file() or OAUTH_BUILD.read_text(encoding="utf-8") != body:
        OAUTH_BUILD.write_text(body, encoding="utf-8")
    say("Встроенные OAuth-клиенты: Google — %s, Notion — %s, Antigravity — %s." % (
        "да" if values["GOOGLE_CLIENT_ID"] else "нет", "да" if values["NOTION_CLIENT_ID"] else "нет",
        "да" if values["ANTIGRAVITY_CLIENT_ID"] else "нет"))


def build_exes(args, version: str) -> None:
    with stage("зависимости (pip)"):
        ensure_venv()
    with stage("иконки, OAuth"):
        ensure_icon()
        write_oauth_build()
    # Round 40: the longest builds start first (MemuAI/Proxy carry the ML and web stacks), memu.exe last.
    keys = [args.only] if args.only else ["memuai", "proxy", "memucode", "memufiles", "memunote", "memu"]
    if "proxy_only" in keys:
        prepare_proxy_only_assets()
    jobs = []
    with stage("проверка изменений (граф импортов)"):
        for key in keys:
            target = TARGETS[key]
            release = target_version(key, version)
            write_version_file(target, release)
            exe = ROOT / target.get("dist", "dist/" + target["name"]) / (target["name"] + ".exe")
            # Round 40: only the files THIS program is built from (target_inputs), not all of app/**.
            stamp = inputs_hash(key + release, target)
            payload_present = not target.get("external_pkg") or exe.with_suffix(".pkg").is_file()
            if not args.force and not args.clean and exe.is_file() and payload_present and read_stamp(key) == stamp:
                say(f"{target['name']}: исходники не менялись — сборка пропущена.")
                continue
            jobs.append((key, target, stamp))
    if not jobs:
        place_memu_cli()
        return

    failures = []
    lock = threading.Lock()
    # Round 40: at most --jobs PyInstaller processes at once (default: one per CPU core, all of them on a
    # normal PC). With a game or another heavy program running, «--jobs 2» keeps the build from fighting it.
    slots = threading.Semaphore(max(1, args.jobs or (os.cpu_count() or 2)))

    def worker(key, target, stamp):
        with slots:
            started = time.time()
            log_path = ROOT / "build" / f"{key}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("w", encoding="utf-8", errors="replace") as log:
                command = pyinstaller_cmd(target, args.clean)
                if target.get("external_pkg"):
                    command = [str(VENV_PY), str(ROOT / "build_freeze.py"), *command[3:]]
                code = subprocess.call(command, cwd=ROOT,
                                       stdout=log, stderr=subprocess.STDOUT)
            took = time.time() - started
        try:
            phases = pyinstaller_phases(log_path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            phases = []
        with lock:
            STAGES.append((f"exe {target['name']}", took))
            if code:
                failures.append(target["name"])
                say(f"{target['name']}: ОШИБКА, подробности в {log_path}")
            else:
                write_stamp(key, stamp)
                detail = ", ".join(f"{name} {seconds:.0f} с" for name, seconds in phases)
                say(f"{target['name']}: готово за {took:.0f} с" + (f" ({detail})" if detail else ""))

    say("Собираю " + " и ".join(t["name"] for _, t, _ in jobs) + " параллельно…")
    with stage("все exe (параллельно)"):
        threads = [threading.Thread(target=worker, args=job) for job in jobs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    if failures and len(jobs) > 1:
        # Параллельные PyInstaller делят общий кэш бинарников. Если что-то
        # пошло не так, один последовательный повтор почти всегда помогает.
        say("Повторяю неудавшиеся сборки по очереди…")
        retry = [job for job in jobs if job[1]["name"] in failures]
        failures.clear()
        for job in retry:
            worker(*job)
    if failures:
        raise SystemExit("Сборка не удалась: " + ", ".join(failures))
    place_memu_cli()


def place_memu_cli() -> None:
    """Round 35 (protocol): memu.exe ships inside the MemuAI folder (its own _internal, never merged)."""
    source, app_dir = ROOT / "dist" / "memu", ROOT / "dist" / "MemuAI"
    if not (source / "memu.exe").is_file() or not app_dir.is_dir():
        return
    target = app_dir / "MemuCLI"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    say("memu.exe → dist/MemuAI/MemuCLI")


def build_installer(args, version: str) -> None:
    """MemuAI-Setup-<версия>.exe: собственный установщик (installer/), полный комплект."""
    from installer import build_setup
    build_setup.build_setup(sys.modules[__name__], "memuai", version, fast=args.fast, clean=args.clean,
                            force=args.force, out=args.out, say=say)


def build_proxy_installer(args, version: str) -> None:
    """MemuAIProxy-Setup-<версия>.exe: отдельный MemuAI Proxy со своей папкой данных."""
    from installer import build_setup
    build_setup.build_setup(sys.modules[__name__], "proxy", version, fast=args.fast, clean=args.clean,
                            force=args.force, out=args.out, say=say)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("what", nargs="?", default="installer", choices=["exe", "installer", "proxy-installer", "version"])
    parser.add_argument("--clean", action="store_true", help="сбросить кэш PyInstaller")
    parser.add_argument("--force", action="store_true", help="пересобрать даже без изменений")
    parser.add_argument("--fast", action="store_true", help="быстрое сжатие установщика (для тестов)")
    parser.add_argument("--skip-exe", action="store_true", help="не пересобирать exe")
    parser.add_argument("--only", choices=list(TARGETS), help="собрать только одну программу")
    parser.add_argument("--out", default="", help="папка для установщика (по умолчанию installer)")
    parser.add_argument("--jobs", type=int, default=0,
                        help="сколько exe собирать одновременно (по умолчанию — по числу ядер)")
    args = parser.parse_args()
    version = app_version()
    started = time.time()
    if args.what == "version":
        for key, target in TARGETS.items():
            write_version_file(target, target_version(key, version))
        say(version)
        return
    if args.what == "proxy-installer":
        args.only = "proxy_only"
    if not args.skip_exe:
        build_exes(args, version)
    if args.what == "installer":
        with stage("установщик (упаковка xz)"):
            build_installer(args, version)
    elif args.what == "proxy-installer":
        with stage("установщик Proxy (упаковка xz)"):
            build_proxy_installer(args, version)
    stage_report()
    say(f"Всё готово за {time.time() - started:.0f} с. Версия {version}.")


if __name__ == "__main__":
    main()
