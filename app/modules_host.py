# -*- coding: utf-8 -*-
"""In-app module host.

A module is a small application that runs inside the MemuAI window: a package
built from ``manifest.json``, ``index.html``, ``style.css``, ``main.js`` and an
optional ``i18n.json`` with the module's translations.
The three shipped apps are ordinary packages on that surface and start
disabled, exactly like a module a person made themselves.

Layout on disk (``%APPDATA%/MemuAI/memuai/modules``)::

    state.json          switches, launcher slot, window geometry, order
    persist/<id>.json   the keys a module is allowed to remember
    custom/<id>/        packages the person made
    overrides/<id>/     their edited copy of a built-in package

The package never talks to the network. Every capability goes through the
bridge in this file: one document, one message channel, one permission list.
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import module_appbar as _module_appbar
from .config import DATA_DIR
from .path_safety import is_link
from .storage import _json_transaction, load_json_with_recovery, mutate_json, save_json

# --------------------------------------------------------------------------
# Константы и пределы
# --------------------------------------------------------------------------

BUILTIN_ORDER: Tuple[str, ...] = ("translator", "studio", "design", "benchmarks", "catalog", "api-models", "characters", "music", "learn")

#: Где в интерфейсе может стоять кнопка модуля.
LAUNCHER_SLOTS: Tuple[str, ...] = ("sidebar", "chat-list", "topbar", "composer")

#: Права, которые автор может запросить в манифесте. Если список не задан,
#: модуль получает все права: так старые пакеты продолжают работать.
PERMISSIONS: Tuple[str, ...] = (
    "chat", "media", "storage", "models", "files",
    "download", "notify", "external", "web", "clipboard", "background",
    # Round 29 (app/character_import.py): download a character card / lorebook by link (Memu.fetchImport).
    "net-import",
    # Round 37 (app/music/api.py): the MemuMusic library, downloader and player (Memu.music.call).
    "music",
    # Round 40 (app/learn/api.py, r40-learn): MemuLearn courses, cards and FSRS reviews (Memu.learn.call).
    "learn",
)
#: Права, которые получает модуль без поля ``permissions`` (старые пакеты). Сетевой
#: импорт и музыкальная библиотека сюда не входят: их надо объявить явно.
IMPLICIT_PERMISSIONS: Tuple[str, ...] = tuple(name for name in PERMISSIONS if name not in ("net-import", "music", "learn"))

#: Действие моста -> требуемое право.
ACTION_PERMISSION: Dict[str, str] = {
    "request": "chat", "stream": "chat", "chat_history": "chat",
    "image": "media", "video": "media", "speak": "media", "transcribe": "media",
    "record": "media", "music": "media", "monitor": "background",
    "save": "storage",
    "models": "models",
    "extract": "files",
    "download": "download", "handoff": "download",
    "notify": "notify",
    "open": "external",
    "search": "web", "fetch": "web",
    "clipboard": "clipboard", "clipboard_read": "clipboard",
    # Round 29 (app/characters_api.py): full-message streaming and the file store.
    "complete": "chat", "store": "storage",
    # Round 29 phase 2 (app/character_import.py): import by link through MemuAI, never from the module.
    "net_import": "net-import",
    # Round 37 (app/music/api.py): MemuMusic operations — Memu.music.call(op, args).
    "music_call": "music",
    # Round 40 (app/learn/api.py, r40-learn): MemuLearn operations — Memu.learn.call(op, args).
    "learn_call": "learn",
}

#: Действия, которые не требуют права: среда, маршруты, размер окна.
#: ``asset`` читает файлы из папки assets/ собственного пакета модуля.
FREE_ACTIONS = ("routes", "env", "resize", "log", "close", "host", "asset", "toolbar", "unsaved", "appbar", "integration")

MEDIA_ROUTES: Dict[str, Dict[str, Any]] = {
    "music": {"available": True, "method": "POST", "path": "/api/pg/modules/music",
              "stream": False, "kind": "audio", "note_ru": "Музыкальный API с шаблоном запроса и опросом задания.",
              "note_en": "Music API using a request template and job polling."},
    "monitor": {"available": True, "method": "POST", "path": "/api/pg/modules/monitor",
                "stream": False, "kind": "json", "note_ru": "Фоновое наблюдение за источниками, пока запущен Proxy.",
                "note_en": "Persistent source observations while the Proxy is running."},
    "chat": {"available": True, "method": "POST", "path": "/api/pg/modules/request",
             "stream": True, "kind": "chat",
             "note_ru": "Ответ модели. Потоковый режим включается через Memu.stream.",
             "note_en": "A model answer. Use Memu.stream for a live stream."},
    "image": {"available": True, "method": "POST", "path": "/api/pg/image",
              "stream": True, "kind": "image",
              "note_ru": "Генерация изображений. Ошибка приходит кадром потока.",
              "note_en": "Image generation. Failures arrive as a stream frame."},
    "video": {"available": True, "method": "POST", "path": "/api/pg/video",
              "stream": True, "kind": "video",
              "note_ru": "Генерация видео тем же конвейером, что и изображения.",
              "note_en": "Video generation through the same pipeline as images."},
    "audio": {"available": True, "method": "POST", "path": "/api/voice/speak",
              "stream": False, "kind": "audio",
              "note_ru": "Озвучка текста: локальные голоса Windows или TTS API.",
              "note_en": "Speech synthesis: Windows voices or a TTS API."},
    "transcribe": {"available": True, "method": "POST", "path": "/api/voice/transcribe",
                   "stream": False, "kind": "text",
                   "note_ru": "Расшифровка голосового файла (data-URL).",
                   "note_en": "Transcribe an audio file sent as a data URL."},
    "extract": {"available": True, "method": "POST", "path": "/api/pg/attachments/extract",
                "stream": False, "kind": "text",
                "note_ru": "Текст из PDF, DOCX и обычных файлов тем же разбором, что и в чате.",
                "note_en": "Text from PDF, DOCX and plain files, parsed like the chat does."},
    "download": {"available": True, "method": "POST", "path": "/api/pg/download-file",
                 "stream": False, "kind": "file",
                 "note_ru": "Сохраняет файл в «Загрузки/MemuAI» и возвращает путь.",
                 "note_en": "Saves a file into Downloads/MemuAI and returns its path."},
    "notify": {"available": True, "method": "POST", "path": "/api/pg/notify",
               "stream": False, "kind": "text",
               "note_ru": "Уведомление MemuAI и Windows.",
               "note_en": "A MemuAI and Windows notification."},
    "search": {"available": True, "method": "POST", "path": "/api/pg/tools",
               "stream": False, "kind": "json",
               "note_ru": "Настроенный веб-поиск MemuAI (DuckDuckGo, SearXNG, Tavily).",
               "note_en": "The configured MemuAI web search (DuckDuckGo, SearXNG, Tavily)."},
    "fetch": {"available": True, "method": "POST", "path": "/api/pg/tools",
              "stream": False, "kind": "text",
              "note_ru": "Чтение одной страницы с ограничением размера.",
              "note_en": "Reads one page with a size limit."},
    "models": {"available": True, "method": "POST", "path": "/api/pg/modules/models",
               "stream": False, "kind": "json",
               "note_ru": "Список моделей MemuAI с возможностями.",
               "note_en": "The MemuAI model list with capabilities."},
}

_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,40}$")
#: Round 33: «/* @memu-part:ai-create.js */» — a file of the same module package inlined into main.js (load_package).
_PART_RE = re.compile(r"/\* @memu-part:((?:integration/)?[a-z0-9][a-z0-9_-]{0,60}\.js) \*/")   # r38: + integration/
#: Round 38: the same for styles — «/* @memu-part:feat.css */» in style.css (MemuMusic keeps feature styles apart).
_CSS_PART_RE = re.compile(r"/\* @memu-part:([a-z0-9][a-z0-9_-]{0,60}\.css) \*/")
#: A locale tag: "ru", "en", "pt-BR", "zh-Hant".
_LOCALE_RE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z]{2,4})?$")
_I18N_LOCALES = 24
_I18N_KEYS = 3000
_I18N_VALUE = 20_000
_PLURAL_FORMS = ("zero", "one", "two", "few", "many", "other")
# Round 33: 400 000 → 1 000 000 — «Characters» (a SillyTavern-sized module) reached ~340 000 characters with the
# inlined token estimate; its «Create with AI» part would not fit. The limit only guards against runaway packages.
_CODE_LIMIT = 1_000_000
_FIELD_LIMIT = 40
_PERSIST_LIMIT = 32
_TEXT_LIMIT = 2_000_000

#: Шаблоны, которые предлагает мастер создания модуля. Каждый сразу
#: двуязычный: тексты разметки помечены ``data-i18n``, строки кода идут через
#: ``Memu.t``, а словари лежат в ``i18n``. Так новый модуль с первой минуты
#: переключается вместе с языком MemuAI и служит примером для автора.
TEMPLATES: Tuple[Dict[str, Any], ...] = (
    {
        "id": "blank",
        "title_ru": "Пустой", "title_en": "Blank",
        "summary_ru": "Минимум разметки: заголовок, поле и кнопка.",
        "summary_en": "The bare minimum: a heading, a field and a button.",
        "icon": "text",
        "html": (
            '<div class="mu-page">\n'
            '  <div class="mu-title" data-i18n="title"></div>\n'
            '  <p class="mu-sub" data-i18n="subtitle"></p>\n'
            '  <div class="mu-card">\n'
            '    <label class="mu-field" for="input" data-i18n="input.label"></label>\n'
            '    <input class="mu-input" id="input" data-i18n-placeholder="input.placeholder">\n'
            '    <div class="mu-row"><button class="mu-btn primary" id="go" data-i18n="send"></button></div>\n'
            '    <div class="mu-out" id="out"></div>\n'
            '  </div>\n'
            '</div>\n'
        ),
        "css": "",
        "js": (
            'const input = document.querySelector("#input");\n'
            'const out = document.querySelector("#out");\n'
            'if (Memu.saved.input) input.value = Memu.saved.input;\n'
            'document.querySelector("#go").onclick = async () => {\n'
            '  out.textContent = Memu.t("thinking");\n'
            '  try {\n'
            '    // Модель из манифеста, иначе первая доступная: без неё сервер\n'
            '    // отвечал «Выберите модель».\n'
            '    const rows = Memu.manifest.bridge.model ? [] : await Memu.models();\n'
            '    const answer = await Memu.request({\n'
            '      model: Memu.manifest.bridge.model || ((rows || []).find((row) => row && !row.image) || {}).id || "",\n'
            '      instructions: Memu.manifest.bridge.instructions,\n'
            '      text: input.value,\n'
            '    });\n'
            '    out.textContent = answer.text || Memu.t("empty");\n'
            '    await Memu.save({ input: input.value });\n'
            '  } catch (error) { out.textContent = error.message; }\n'
            '};\n'
        ),
        "i18n": {
            "ru": {"title": "Мой модуль", "subtitle": "Опишите здесь, что делает ваше приложение.",
                   "input.label": "Ввод", "input.placeholder": "Напишите что-нибудь",
                   "send": "Отправить в модель", "thinking": "Думаю…", "empty": "Пустой ответ"},
            "en": {"title": "My module", "subtitle": "Describe what your app does here.",
                   "input.label": "Input", "input.placeholder": "Type something",
                   "send": "Send to the model", "thinking": "Thinking…", "empty": "Empty answer"},
        },
        "persist": ["input"],
        "permissions": ["chat", "models", "storage"],
    },
    {
        "id": "chat",
        "title_ru": "Мини-чат", "title_en": "Mini chat",
        "summary_ru": "Диалог с моделью, история и потоковый ответ.",
        "summary_en": "A conversation with a model, history and a live stream.",
        "icon": "chat",
        "html": (
            '<div class="mu-page mu-col">\n'
            '  <div class="mu-toolbar">\n'
            '    <select class="mu-select" id="model" data-i18n-aria-label="model"></select>\n'
            '    <span class="mu-grow"></span>\n'
            '    <button class="mu-btn ghost" id="reset" data-i18n="clear"></button>\n'
            '  </div>\n'
            '  <div class="mu-scroll mu-log" id="log"></div>\n'
            '  <div class="mu-composer">\n'
            '    <textarea class="mu-textarea" id="text" rows="1" data-i18n-placeholder="placeholder"></textarea>\n'
            '    <button class="mu-btn primary" id="send" data-i18n="send"></button>\n'
            '  </div>\n'
            '</div>\n'
        ),
        "css": ".mu-log{flex:1;min-height:0;overflow:auto;display:flex;flex-direction:column;gap:8px;padding:10px}"
                 ".msg{padding:8px 11px;border-radius:11px;background:var(--panel-2);max-width:78%;white-space:pre-wrap}"
                 ".msg.me{align-self:flex-end;background:var(--accent-soft);color:var(--text)}",
        "js": (
            'let history = Array.isArray(Memu.saved.history) ? Memu.saved.history : [];\n'
            'const log = document.querySelector("#log"), text = document.querySelector("#text");\n'
            'const model = document.querySelector("#model");\n'
            'Memu.models().then((rows) => {\n'
            '  model.innerHTML = (rows || []).map((item) => `<option value="${item.id}">${item.id}</option>`).join("");\n'
            '  if (Memu.saved.model) model.value = Memu.saved.model;\n'
            '});\n'
            'function draw() {\n'
            '  log.innerHTML = history.length\n'
            '    ? history.map((m) => `<div class="msg ${m.role === "user" ? "me" : ""}">${escapeHtml(m.content)}</div>`).join("")\n'
            '    : `<div class="mu-empty">${escapeHtml(Memu.t("empty"))}</div>`;\n'
            '  log.scrollTop = log.scrollHeight;\n'
            '}\n'
            'function escapeHtml(v) { return String(v).replace(/[&<>]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }\n'
            'draw();\n'
            '// Строки, собранные кодом, перерисовываются при смене языка MemuAI.\n'
            'Memu.onLanguage(draw);\n'
            'document.querySelector("#reset").onclick = async () => { history = []; draw(); await Memu.save({ history: [] }); };\n'
            'document.querySelector("#send").onclick = async () => {\n'
            '  const value = text.value.trim();\n'
            '  if (!value) return;\n'
            '  text.value = "";\n'
            '  history.push({ role: "user", content: value });\n'
            '  const last = { role: "assistant", content: "" };\n'
            '  history.push(last); draw();\n'
            '  try {\n'
            '    await Memu.stream({ model: model.value, history, text: value }, {\n'
            '      onDelta(chunk) { last.content += chunk; draw(); },\n'
            '    });\n'
            '    await Memu.save({ history, model: model.value });\n'
            '  } catch (error) { last.content = Memu.t("error", { message: error.message }); draw(); }\n'
            '};\n'
        ),
        "i18n": {
            "ru": {"model": "Модель", "clear": "Очистить", "placeholder": "Сообщение…", "send": "Отправить",
                   "empty": "Здесь появится диалог", "error": "Ошибка: {message}"},
            "en": {"model": "Model", "clear": "Clear", "placeholder": "Message…", "send": "Send",
                   "empty": "The conversation appears here", "error": "Error: {message}"},
        },
        "persist": ["history", "model"],
        "permissions": ["chat", "storage", "models"],
    },
    {
        "id": "tool",
        "title_ru": "Инструмент", "title_en": "Tool",
        "summary_ru": "Форма с полями, вычислением и сохранением настроек.",
        "summary_en": "A form with fields, local logic and saved settings.",
        "icon": "zap",
        "html": (
            '<div class="mu-page">\n'
            '  <div class="mu-title" data-i18n="title"></div>\n'
            '  <div class="mu-card mu-grid">\n'
            '    <label class="mu-field"><span data-i18n="first"></span><input class="mu-input" id="a" type="number" value="0"></label>\n'
            '    <label class="mu-field"><span data-i18n="second"></span><input class="mu-input" id="b" type="number" value="0"></label>\n'
            '    <label class="mu-field"><span data-i18n="operation"></span><select class="mu-select" id="op">'
            '<option value="+" data-i18n="add"></option><option value="-" data-i18n="subtract"></option>'
            '<option value="*" data-i18n="multiply"></option><option value="/" data-i18n="divide"></option></select></label>\n'
            '  </div>\n'
            '  <div class="mu-row"><button class="mu-btn primary" id="calc" data-i18n="calculate"></button></div>\n'
            '  <div class="mu-result" id="out">—</div>\n'
            '</div>\n'
        ),
        "css": ".mu-result{margin-top:14px;font-size:30px;font-weight:650;color:var(--accent)}",
        "js": (
            'const fields = ["a", "b", "op"];\n'
            'fields.forEach((id) => { if (Memu.saved[id] != null) document.querySelector("#" + id).value = Memu.saved[id]; });\n'
            'document.querySelector("#calc").onclick = async () => {\n'
            '  const a = Number(document.querySelector("#a").value) || 0;\n'
            '  const b = Number(document.querySelector("#b").value) || 0;\n'
            '  const op = document.querySelector("#op").value;\n'
            '  const value = op === "+" ? a + b : op === "-" ? a - b : op === "*" ? a * b : (b === 0 ? NaN : a / b);\n'
            '  // Числа форматируются по языку MemuAI: 1 234,5 или 1,234.5.\n'
            '  document.querySelector("#out").textContent = Number.isNaN(value) ? "∞" : Memu.i18n.number(value);\n'
            '  await Memu.save({ a, b, op }).catch(() => {});\n'
            '};\n'
        ),
        "i18n": {
            "ru": {"title": "Калькулятор", "first": "Первое число", "second": "Второе число",
                   "operation": "Действие", "add": "Сложить", "subtract": "Вычесть",
                   "multiply": "Умножить", "divide": "Разделить", "calculate": "Посчитать"},
            "en": {"title": "Calculator", "first": "First number", "second": "Second number",
                   "operation": "Operation", "add": "Add", "subtract": "Subtract",
                   "multiply": "Multiply", "divide": "Divide", "calculate": "Calculate"},
        },
        "persist": ["a", "b", "op"],
        "permissions": ["storage"],
    },
    {
        "id": "media",
        "title_ru": "Медиа", "title_en": "Media",
        "summary_ru": "Генерация изображения по описанию через модели MemuAI.",
        "summary_en": "Generate an image from a description through MemuAI models.",
        "icon": "image",
        "html": (
            '<div class="mu-page mu-col">\n'
            '  <div class="mu-toolbar">\n'
            '    <select class="mu-select" id="model" data-i18n-aria-label="model"></select>\n'
            '    <select class="mu-select" id="aspect" data-i18n-aria-label="aspect"><option>16:9</option><option>1:1</option>'
            '<option>9:16</option></select>\n'
            '  </div>\n'
            '  <textarea class="mu-textarea" id="prompt" rows="3" data-i18n-placeholder="prompt"></textarea>\n'
            '  <div class="mu-row"><button class="mu-btn primary" id="go" data-i18n="generate"></button>'
            '<span class="mu-muted" id="status"></span><span class="mu-grow"></span>'
            '<button class="mu-btn ghost" id="save" data-i18n="download" hidden></button></div>\n'
            '  <div class="mu-out" id="out"></div>\n'
            '</div>\n'
        ),
        "css": "#out img{max-width:100%;border-radius:12px}",
        "js": (
            'const status = document.querySelector("#status"), out = document.querySelector("#out");\n'
            'let last = "";\n'
            'Memu.models().then((rows) => {\n'
            '  const picks = (rows || []).filter((item) => item.image);\n'
            '  const list = picks.length ? picks : (rows || []);\n'
            '  document.querySelector("#model").innerHTML = list.map((i) => `<option value="${i.id}">${i.id}</option>`).join("");\n'
            '  if (Memu.saved.model) document.querySelector("#model").value = Memu.saved.model;\n'
            '});\n'
            'if (Memu.saved.prompt) document.querySelector("#prompt").value = Memu.saved.prompt;\n'
            'if (Memu.saved.aspect) document.querySelector("#aspect").value = Memu.saved.aspect;\n'
            'document.querySelector("#save").onclick = () => Memu.download({ url: last, name: "image.png" }).catch((e) => { status.textContent = e.message; });\n'
            'document.querySelector("#go").onclick = async () => {\n'
            '  status.textContent = Memu.t("drawing"); out.innerHTML = "";\n'
            '  Memu.save({ prompt: document.querySelector("#prompt").value, model: document.querySelector("#model").value,\n'
            '    aspect: document.querySelector("#aspect").value }).catch(() => {});\n'
            '  try {\n'
            '    const result = await Memu.image({ prompt: document.querySelector("#prompt").value, model: document.querySelector("#model").value, aspect: document.querySelector("#aspect").value });\n'
            '    last = (result.urls || [])[0] || "";\n'
            '    if (last) out.innerHTML = `<img alt="" src="${last}">`; else out.textContent = Memu.t("no_image");\n'
            '    document.querySelector("#save").hidden = !last;\n'
            '    status.textContent = last ? Memu.t("done") : Memu.t("empty");\n'
            '  } catch (error) { status.textContent = error.message; }\n'
            '};\n'
        ),
        "i18n": {
            "ru": {"model": "Модель", "aspect": "Соотношение сторон", "prompt": "Опишите изображение",
                   "generate": "Сгенерировать", "download": "Скачать", "drawing": "Рисую…",
                   "no_image": "Провайдер не вернул изображение", "done": "Готово", "empty": "Пустой результат"},
            "en": {"model": "Model", "aspect": "Aspect ratio", "prompt": "Describe the image",
                   "generate": "Generate", "download": "Download", "drawing": "Drawing…",
                   "no_image": "The provider returned no image", "done": "Done", "empty": "Empty result"},
        },
        "persist": ["prompt", "model", "aspect"],
        "permissions": ["media", "models", "download", "storage"],
    },
)


def default_root() -> Path:
    return Path(DATA_DIR) / "memuai" / "modules"


def builtin_dir() -> Path:
    """Where the shipped packages live, inside a frozen app too.

    PyInstaller puts ``app/module_builtins`` next to the bundled ``app``
    package in ``sys._MEIPASS``. Reading it from ``__file__`` alone finds
    nothing in the packaged ``.exe``, which used to open an empty window.
    """
    import sys

    base = getattr(sys, "_MEIPASS", "")
    if base:
        frozen = Path(base) / "app" / "module_builtins"
        if frozen.is_dir():
            return frozen
    return Path(__file__).resolve().parent / "module_builtins"


def media_routes() -> Dict[str, Dict[str, Any]]:
    return {key: dict(value) for key, value in MEDIA_ROUTES.items()}


def templates() -> List[Dict[str, Any]]:
    # Права и сохраняемые поля нужны мастеру: без них шаблон «Мини-чат»
    # создавался без права models и падал на первом же Memu.models().
    return [
        {
            "id": row["id"],
            "title_ru": row["title_ru"], "title_en": row["title_en"],
            "summary_ru": row["summary_ru"], "summary_en": row["summary_en"],
            "icon": row["icon"],
            "permissions": list(row["permissions"]),
            "persist": list(row["persist"]),
        }
        for row in TEMPLATES
    ]


def template_manifest(template_id: str, module_id: str, title: str) -> Dict[str, Any]:
    row = next((item for item in TEMPLATES if item["id"] == template_id), None)
    if row is None:
        raise KeyError(template_id)
    window = {"default_width": 760, "default_height": 560}
    if template_id == "chat":
        window = {"default_width": 720, "default_height": 600}
    if template_id == "media":
        window = {"default_width": 820, "default_height": 620}
    if template_id == "tool":
        window = {"default_width": 560, "default_height": 460}
    return {
        "id": module_id, "title": title or row["title_ru"], "title_en": title or row["title_en"],
        "html": row["html"], "css": row["css"], "js": row["js"],
        "i18n": json.loads(json.dumps(row["i18n"])),
        "persist": list(row["persist"]), "permissions": list(row["permissions"]),
        "icon": row["icon"], "window": window,
        "summary": row["summary_ru"], "summary_en": row["summary_en"],
    }


# --------------------------------------------------------------------------
# Низкоуровневые помощники
# --------------------------------------------------------------------------

def _read_json(path: Path) -> Dict[str, Any]:
    value = load_json_with_recovery(path, {}, validator=lambda item: isinstance(item, dict))
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: Dict[str, Any], *, backup: bool = False) -> None:
    save_json(str(path), value, backup=backup)


@contextmanager
def _package_transaction(folder: Path):
    # The lock identity must survive both directory renames at commit time.
    with _json_transaction(folder.parent / f".{folder.name}.package"):
        _recover_package(folder)
        yield


def _recover_package(folder: Path) -> None:
    if folder.exists():
        return
    retained = [path for path in folder.parent.glob(f".{folder.name}.previous-*")
                if re.fullmatch(rf"\.{re.escape(folder.name)}\.previous-[0-9a-f]{{32}}", path.name)
                and path.is_dir()]
    # Never choose between ambiguous retained versions or overwrite live data.
    if len(retained) == 1:
        previous = retained[0]
        _reject_package_links(previous)
        os.replace(previous, folder)


def _clamp_int(value: Any, fallback: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    return max(low, min(high, number))


def _clean_text(value: Any, limit: int) -> str:
    return str(value or "")[:limit]


def _script_json(value: Any) -> str:
    """JSON that cannot leave its ``<script>`` block."""
    return (json.dumps(value, ensure_ascii=False)
            .replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def _embed_script(text: str) -> str:
    """Make source text safe inside a ``<script>`` block."""
    return re.sub(r"</(\s*script)", r"<\\/\1", str(text or ""), flags=re.I)


def _embed_style(text: str) -> str:
    """Styles must not escape their ``<style>`` block."""
    return re.sub(r"</\s*(style|head|body|html)\s*>", "", str(text or ""), flags=re.I)


def _embed_html(text: str) -> str:
    """A module returns a fragment; it must not close the host document."""
    return re.sub(r"</\s*(html|body|head)\s*>", "", str(text or ""), flags=re.I)


def normalize_i18n(value: Any) -> Dict[str, Dict[str, Any]]:
    """Translations of a module: ``{locale: {key: text | {plural form: text}}}``.

    A JSON string is accepted too (that is what the workbench editor sends);
    malformed JSON raises ``ValueError`` so the author sees the mistake instead
    of silently losing every string. Unknown shapes are dropped, sizes capped.
    """
    if isinstance(value, str):
        if not value.strip():
            return {}
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Переводы: неверный JSON в строке {exc.lineno}, столбце {exc.colno}") from exc
    if not isinstance(value, dict):
        if value in (None, [], ""):
            return {}
        raise ValueError("Переводы должны быть объектом вида {\"ru\": {...}, \"en\": {...}}")
    result: Dict[str, Dict[str, Any]] = {}
    for locale, table in list(value.items())[:_I18N_LOCALES]:
        if not isinstance(locale, str) or not _LOCALE_RE.match(locale) or not isinstance(table, dict):
            continue
        clean: Dict[str, Any] = {}
        for key, text in list(table.items())[:_I18N_KEYS]:
            key = str(key)[:200]
            if not key:
                continue
            if isinstance(text, dict):
                forms = {form: str(text[form])[:_I18N_VALUE] for form in _PLURAL_FORMS
                         if isinstance(text.get(form), (str, int, float))}
                if forms:
                    clean[key] = forms
            elif isinstance(text, (str, int, float)) and not isinstance(text, bool):
                clean[key] = str(text)[:_I18N_VALUE]
        result[locale] = clean
    return result


def _read_i18n(folder: Path) -> Dict[str, Dict[str, Any]]:
    """The package's translations; a broken file must not stop the module opening."""
    try:
        return normalize_i18n(_text_file(folder, "i18n.json"))
    except ValueError:
        return {}


def i18n_keys_used(html_text: str, js_text: str) -> List[str]:
    """Keys the markup and the script ask for, in first-seen order."""
    keys: List[str] = []
    for match in re.finditer(r'data-i18n(?:-[a-z-]+)?\s*=\s*["\']([^"\']+)["\']', html_text or ""):
        keys.append(match.group(1))
    # Only a whole literal argument counts: Memu.t("mode." + name) builds its key at run time.
    for match in re.finditer(r'\bMemu\.(?:t|i18n\.t)\(\s*(["\'`])([^"\'`$]+)\1\s*[,)]', js_text or ""):
        keys.append(match.group(2))
    for match in re.finditer(r'data-i18n(?:-[a-z-]+)?\s*=\s*\\?["\']([A-Za-z0-9_.-]+)\\?["\']', js_text or ""):
        keys.append(match.group(1))
    return list(dict.fromkeys(keys))


def _icon_name(value: Any) -> str:
    name = re.sub(r"[^a-z0-9_-]", "", str(value or "").strip().lower())[:32]
    return name


def _icon_value(value: Any) -> str:
    """Round 39: a glyph name, a package file, a clean inline SVG or a data URL (app/module_icons.py)."""
    from .module_icons import normalize_icon
    return normalize_icon(value)


def _icon_bg_block(source: Dict[str, Any]) -> Dict[str, Any]:
    from .module_icons import normalize_icon_bg
    colours = normalize_icon_bg(source.get("icon_bg"))
    return {"icon_bg": colours} if colours else {}


def _with_icons(root: Path, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Round 39: ``icon_url`` (the user's icon, else the manifest's image), ``icon_bg``, ``icon_custom`` on each row."""
    from .module_icons import load_user, public_fields
    user = load_user(root)
    for row in rows:
        row.update(public_fields(root, row["id"], {"icon": row.get("icon_src"), "icon_bg": row.get("icon_bg")}, user))
    return rows


# --------------------------------------------------------------------------
# Чтение состояния
# --------------------------------------------------------------------------

def load_state(root: Path) -> Dict[str, Any]:
    state = _read_json(Path(root) / "state.json")
    modules = state.get("modules")
    state["modules"] = modules if isinstance(modules, dict) else {}
    return state


def _mutate_state(root: Path, module_id: str, mutator) -> Dict[str, Any]:
    """One atomic read-modify-write for a single module row."""
    module_id = _safe_id(module_id)

    def apply(raw: Any) -> Tuple[bool, Any]:
        data = raw if isinstance(raw, dict) else {}
        rows = data.get("modules")
        if not isinstance(rows, dict):
            rows = {}
        row = rows.get(module_id)
        row = dict(row) if isinstance(row, dict) else {}
        mutator(row)
        rows[module_id] = row
        return True, {"modules": rows}

    result = mutate_json(Path(root) / "state.json", {"modules": {}}, apply, backup=False,
                         validator=lambda value: isinstance(value, dict), repair_invalid=True)
    rows = result.get("modules") if isinstance(result, dict) else {}
    row = rows.get(module_id) if isinstance(rows, dict) else None
    return row if isinstance(row, dict) else {}


def _manifest_file(folder: Path) -> Dict[str, Any]:
    manifest = _read_json(folder / "manifest.json")
    if not manifest.get("id"):
        manifest["id"] = folder.name
    return manifest


def _text_file(folder: Path, name: str) -> str:
    path = folder / name
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


# --------------------------------------------------------------------------
# Манифест
# --------------------------------------------------------------------------

def normalize_window(spec: Any) -> Dict[str, Any]:
    """Author bounds, sane and never degenerate.

    Order matters: the drag limits are settled first, then a stretch of room is
    guaranteed, and only then is the default size pulled inside. Doing it the
    other way round let ``min_width`` and ``max_width`` meet at 2400, so an
    author asking for huge numbers got a window marked resizable that could not
    be resized at all.
    """
    window = spec if isinstance(spec, dict) else {}
    fixed = bool(window.get("fixed"))
    width = _clamp_int(window.get("default_width", window.get("width")), 760, 240, 2400)
    height = _clamp_int(window.get("default_height", window.get("height")), 540, 200, 1600)
    # Минимум не больше самого окна, но с запасом на растягивание ниже потолка.
    min_width = _clamp_int(window.get("min_width"), min(320, width), 200, 2000)
    min_height = _clamp_int(window.get("min_height"), min(240, height), 140, 1200)
    max_width = _clamp_int(window.get("max_width"), max(width, 1600),
                           min(min_width + 200, 4000), 4000)
    max_height = _clamp_int(window.get("max_height"), max(height, 1200),
                            min(min_height + 150, 4000), 4000)
    return {
        "default_width": max(min_width, min(max_width, width)),
        "default_height": max(min_height, min(max_height, height)),
        "min_width": min_width,
        "min_height": min_height,
        "max_width": max_width,
        "max_height": max_height,
        "fixed": fixed,
        "resizable": False if fixed else bool(window.get("resizable", True)),
        "draggable": window.get("draggable"),
        "size_mode": "authored" if str(window.get("size_mode") or "user") == "authored" else "user",
        "center": bool(window.get("center", True)),
        "fullscreen_allowed": bool(window.get("fullscreen_allowed", True)),
        # Round 38: the title bar of the module's own app window (app/module_appbar.py).
        **({"appbar": _module_appbar.normalize(window["appbar"])} if isinstance(window.get("appbar"), dict) else {}),
    }


def normalize_manifest(spec: Dict[str, Any], *, module_id: str,
                       base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Merge a package spec over ``base`` into a complete, valid manifest."""
    spec = spec if isinstance(spec, dict) else {}
    source = dict(base or {})
    source.update({key: value for key, value in (spec or {}).items() if value is not None})
    previous_window = base.get("window") if isinstance((base or {}).get("window"), dict) else {}
    incoming_window = spec.get("window") if isinstance(spec.get("window"), dict) else {}
    previous_window = dict(previous_window)
    # Новый размер больше унаследованного потолка — потолок не наследуем,
    # иначе ширина 2000 молча сохранялась как 1600.
    for size, limit in (("default_width", "max_width"), ("default_height", "max_height")):
        try:
            if limit not in incoming_window and size in incoming_window and \
                    int(incoming_window[size]) > int(previous_window.get(limit) or 0):
                previous_window.pop(limit, None)
        except (TypeError, ValueError):
            pass
    window = normalize_window({**previous_window, **incoming_window,
                               "default_width": incoming_window.get("default_width",
                                                                    previous_window.get("default_width", 760)),
                               "default_height": incoming_window.get("default_height",
                                                                     previous_window.get("default_height", 540))})
    launcher = spec.get("launcher") if isinstance(spec.get("launcher"), dict) else {}
    previous_launcher = base.get("launcher") if isinstance((base or {}).get("launcher"), dict) else {}
    slot = str(launcher.get("slot") or previous_launcher.get("slot") or "sidebar")
    if slot not in LAUNCHER_SLOTS:
        slot = "sidebar"
    persist = source.get("persist") if isinstance(source.get("persist"), list) else []
    permissions = source.get("permissions")
    if "permissions" not in source or permissions is None:
        permissions = list(IMPLICIT_PERMISSIONS)  # поле не указано — все права (правило 9), кроме net-import
    elif not isinstance(permissions, list):
        # «permissions": "chat"» или {} — ошибка манифеста, а не «дай всё».
        raise ValueError("permissions должен быть списком")
    clean_permissions = [name for name in permissions if name in PERMISSIONS]
    bridge = spec.get("bridge") if isinstance(spec.get("bridge"), dict) else {}
    previous_bridge = base.get("bridge") if isinstance((base or {}).get("bridge"), dict) else {}
    bridge = {**previous_bridge, **bridge}
    title = _clean_text(source.get("title"), 80) or module_id
    return {
        "schema": 2,
        "id": module_id,
        "title": title,
        "title_en": _clean_text(source.get("title_en"), 80) or title,
        "summary": _clean_text(source.get("summary"), 400),
        "summary_en": _clean_text(source.get("summary_en"), 400),
        "version": _clean_text(source.get("version"), 24) or "1.0",
        "author": _clean_text(source.get("author"), 80),
        # Язык, на который мост откатывается, если нужного перевода нет.
        "default_locale": (str(source.get("default_locale"))
                           if _LOCALE_RE.match(str(source.get("default_locale") or "")) else "ru"),
        "icon": _icon_value(source.get("icon")),
        "accent": _clean_text(source.get("accent"), 24),
        **_icon_bg_block(source),
        "enabled": False,
        "launcher": {
            "hidden": bool(launcher.get("hidden", previous_launcher.get("hidden", False))),
            "slot": slot,
            "order": _clamp_int(launcher.get("order", previous_launcher.get("order")), 0, 0, 9999),
        },
        "window": window,
        "persist": list(dict.fromkeys(
            str(item).strip()[:40] for item in persist if str(item).strip()))[:_PERSIST_LIMIT],
        # Отсутствие списка прав означает «все» (старые модули не должны
        # сломаться), но ЯВНО пустой список — это осознанное «ничего»:
        # раньше `or` превращал его во «все», и снятие всех галочек в
        # мастерской выдавало модулю полный доступ.
        "permissions": clean_permissions,
        "bridge": {
            "model": _clean_text(bridge.get("model"), 300),
            "instructions": _clean_text(bridge.get("instructions"), 20_000),
            "keep_in_chat": bool(bridge.get("keep_in_chat")),
            "thinking": bool(bridge.get("thinking")),
            "web_search": bool(bridge.get("web_search")),
        },
        **_net_import_block(source),
        **_contributes_block(source, module_id),
    }


def _contributes_block(source: Dict[str, Any], module_id: str) -> Dict[str, Any]:
    """Round 38: ``contributes`` (panels, commands, skill, widgets — app/module_integration.py), cleaned; kept on save/import."""
    if not isinstance(source.get("contributes"), dict):
        return {}
    from .module_integration import normalize_contributes
    clean = normalize_contributes(source["contributes"], builtin=module_id in BUILTIN_ORDER)
    return {"contributes": clean} if clean else {}


def _net_import_block(source: Dict[str, Any]) -> Dict[str, Any]:
    """``net_import: {sources, hosts}`` — what a module with ``net-import`` may download (app/character_import.py)."""
    if not isinstance(source.get("net_import"), dict):
        return {}
    from .character_import import normalize_manifest_block
    return {"net_import": normalize_manifest_block(source.get("net_import"))}


def _public_row(manifest: Dict[str, Any], overlay: Dict[str, Any], *, builtin: bool) -> Dict[str, Any]:
    launcher = overlay.get("launcher") if isinstance(overlay.get("launcher"), dict) else {}
    authored = manifest.get("launcher") if isinstance(manifest.get("launcher"), dict) else {}
    slot = str(launcher.get("slot") or authored.get("slot") or "sidebar")
    if slot not in LAUNCHER_SLOTS:
        slot = "sidebar"
    if "enabled" in overlay:
        enabled = bool(overlay.get("enabled"))
    else:
        # Every module — built-in ones included — starts switched off.
        enabled = False if builtin else bool(manifest.get("enabled"))
    window = manifest.get("window") if isinstance(manifest.get("window"), dict) else {}
    bridge = manifest.get("bridge") if isinstance(manifest.get("bridge"), dict) else {}
    return {
        "id": str(manifest.get("id") or ""),
        "title": str(manifest.get("title") or manifest.get("id") or ""),
        "title_en": str(manifest.get("title_en") or manifest.get("title") or ""),
        "summary": str(manifest.get("summary") or ""),
        "summary_en": str(manifest.get("summary_en") or ""),
        "version": str(manifest.get("version") or "1.0"),
        "author": str(manifest.get("author") or ""),
        "default_locale": str(manifest.get("default_locale") or "ru"),
        # Round 39: «icon» stays a glyph name for older code; the image is «icon_url» (_with_icons), the raw value «icon_src».
        "icon": _glyph(manifest),
        "icon_src": str(manifest.get("icon") or ""),
        "icon_bg": list(manifest.get("icon_bg") or []),
        "accent": str(manifest.get("accent") or ""),
        "builtin": builtin,
        "enabled": enabled,
        "launcher": {
            "hidden": bool(launcher.get("hidden", authored.get("hidden", False))),
            "slot": slot,
            "order": _clamp_int(launcher.get("order", authored.get("order")), 0, 0, 9999),
        },
        "window": window,
        "persist": [str(item) for item in (manifest.get("persist") or []) if str(item).strip()][:_PERSIST_LIMIT],
        "permissions": [str(item) for item in (manifest.get("permissions") or []) if str(item) in PERMISSIONS],
        "bridge": {
            "model": str(bridge.get("model") or ""),
            "instructions": str(bridge.get("instructions") or ""),
            "keep_in_chat": bool(bridge.get("keep_in_chat")),
            "thinking": bool(bridge.get("thinking")),
            "web_search": bool(bridge.get("web_search")),
        },
        **({"net_import": manifest["net_import"]} if isinstance(manifest.get("net_import"), dict) else {}),
        "edited": bool(overlay.get("edited")),
        # Менять размер окна может любой модуль, пока человек это не выключил.
        "resizable": overlay.get("resizable") is not False,
        "open": bool(overlay.get("open")),
        "fullscreen": bool(overlay.get("fullscreen")),
    }


def _glyph(manifest: Dict[str, Any]) -> str:
    from .module_icons import glyph_of
    return glyph_of(str(manifest.get("id") or ""), str(manifest.get("icon") or ""))


def builtin_manifests(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    base = builtin_dir()
    for module_id in BUILTIN_ORDER:
        folder = base / module_id
        if root is not None:
            override = Path(root) / "overrides" / module_id
            if not override.exists() and list(override.parent.glob(f".{module_id}.previous-*")):
                with _package_transaction(override):
                    pass
            if (override / "manifest.json").is_file():
                folder = override
        if not folder.is_dir():
            continue
        manifest = normalize_manifest(_manifest_file(folder), module_id=module_id)
        manifest["builtin"] = True
        manifest["enabled"] = False
        rows.append(manifest)
    return rows


def _overlay(state: Dict[str, Any], module_id: str) -> Dict[str, Any]:
    rows = state.get("modules") if isinstance(state.get("modules"), dict) else {}
    row = rows.get(module_id)
    return row if isinstance(row, dict) else {}


def _custom_manifests(root: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    custom_root = Path(root) / "custom"
    if not custom_root.is_dir():
        return rows
    for previous in custom_root.glob(".*.previous-*"):
        match = re.fullmatch(r"\.([a-z][a-z0-9-]{0,40})\.previous-[0-9a-f]{32}", previous.name)
        if match:
            folder = custom_root / match.group(1)
            if not folder.exists():
                with _package_transaction(folder):
                    pass
    for folder in sorted(path for path in custom_root.iterdir() if path.is_dir()):
        # Идентификатор — это имя папки: по нему модуль открывают и удаляют.
        # Модуль с другим id в манифесте был виден, но недоступен.
        module_id = folder.name
        if module_id in BUILTIN_ORDER or not _ID_RE.match(module_id):
            continue
        manifest = _manifest_file(folder)
        try:
            row = normalize_manifest(manifest, module_id=module_id)
        except ValueError:
            continue  # битый манифест не должен ронять весь список модулей
        row["builtin"] = False
        rows.append(row)
    return rows


def _sort_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    order = {module_id: index for index, module_id in enumerate(BUILTIN_ORDER)}
    return sorted(rows, key=lambda row: (
        row["launcher"]["order"],
        0 if row["builtin"] else 1,
        order.get(row["id"], 99),
        row["id"]))


def list_modules(root: Path) -> List[Dict[str, Any]]:
    root = Path(root)
    state = load_state(root)
    rows = [_public_row(manifest, _overlay(state, manifest["id"]), builtin=True)
            for manifest in builtin_manifests(root)
            if not _overlay(state, manifest["id"]).get("removed")]
    for manifest in _custom_manifests(root):
        rows.append(_public_row(manifest, _overlay(state, manifest["id"]), builtin=False))
    return _with_icons(root, _sort_rows(rows))


def _safe_id(module_id: Any) -> str:
    """An id that is safe to build a filesystem path from.

    Every function below reaches the disk, and an id like ``../../config`` used
    to escape the module root — a single request could blank any reachable
    ``.json``, including the app config that holds provider tokens. Nothing is
    trusted here: the id must be a plain lowercase slug, so ``..``, ``/`` and
    ``\\`` cannot appear at all.
    """
    value = str(module_id or "").strip()
    if value != value.lower() or not _ID_RE.match(value):
        raise ValueError(
            "Недопустимый идентификатор модуля: " + repr(str(module_id))[:80])
    return value


def _normalize_env(env: Dict[str, Any]) -> Dict[str, Any]:
    """Coerce the appearance values to a known shape.

    These values are interpolated into HTML attributes of the module document,
    and the preview route accepts them straight from a request body, so an
    unvalidated ``lang`` could close the attribute and inject its own markup.
    Whitelists here, not escaping, because every field has a small known set.
    """
    from . import themes
    densities = ("normal", "compact", "spacious")
    result = dict(env)
    result["lang"] = "en" if str(env.get("lang")) == "en" else "ru"
    result["theme"] = themes.normalize_mode(env.get("theme"))
    result["accent_name"] = themes.normalize_accent(env.get("accent_name"))
    result["surface"] = themes.normalize_variant(result["theme"], env.get("surface"))
    density = str(env.get("density") or "normal")
    result["density"] = density if density in densities else "normal"
    result["accent"] = str(env.get("accent") or "") \
        if re.fullmatch(r"#[0-9a-fA-F]{3,8}", str(env.get("accent") or "")) else ""
    result["module"] = re.sub(r"[^a-z0-9-]", "", str(env.get("module") or "").lower())[:41]
    try:
        scale = int(env.get("font_scale") or 100)
    except (TypeError, ValueError):
        scale = 100
    result["font_scale"] = max(85, min(125, scale))
    # Round 30: "host" — the MemuAI window draws the module's actions in its own
    # title row (Memu.toolbar); anything else — the module shows its own header.
    result["toolbar"] = "host" if str(env.get("toolbar") or "") == "host" else ""
    result["host"] = "MemuAI"
    result["platform"] = sys.platform
    for key in ("host", "platform"):
        result[key] = re.sub(r"[^A-Za-z0-9._-]", "", str(result[key]))[:40]
    return result


def _attr(value: Any) -> str:
    """An attribute value that cannot leave the quotes it sits in."""
    return html.escape(str(value or ""), quote=True)


def _package_dir(root: Path, module_id: str) -> Optional[Path]:
    module_id = _safe_id(module_id)
    custom = Path(root) / "custom" / module_id
    if custom.is_dir():
        return custom
    override = Path(root) / "overrides" / module_id
    if module_id in BUILTIN_ORDER and override.is_dir() and (override / "manifest.json").is_file():
        return override
    builtin = builtin_dir() / module_id
    if module_id in BUILTIN_ORDER and builtin.is_dir():
        return builtin
    return None


def load_package(root: Path, module_id: str) -> Dict[str, Any]:
    module_id = _safe_id(module_id)
    owner = Path(root) / ("overrides" if module_id in BUILTIN_ORDER else "custom") / module_id
    with _package_transaction(owner):
        return _load_package_locked(root, module_id)


def _load_package_locked(root: Path, module_id: str) -> Dict[str, Any]:
    folder = _package_dir(root, module_id)
    if folder is None:
        raise KeyError(module_id)
    builtin = module_id in BUILTIN_ORDER
    if builtin and _removed(root, module_id):
        raise KeyError(module_id)
    manifest = normalize_manifest(_manifest_file(folder), module_id=module_id)
    state = load_state(root)
    row = _public_row(manifest, _overlay(state, module_id), builtin=builtin)
    row["builtin"] = builtin
    _with_icons(root, [row])
    # У изменённой копии встроенного модуля может не хватать файлов (правили
    # только манифест или запись оборвалась): недостающее берём из поставки,
    # иначе окно открывалось пустым.
    fallback = builtin_dir() / module_id if builtin and folder.parent.name == "overrides" else None
    for key, name in (("html", "index.html"), ("css", "style.css"), ("js", "main.js")):
        text = _text_file(folder, name)
        if not text.strip() and fallback is not None:
            text = _text_file(fallback, name)
        row[key] = text
    # Keep the shared benchmark implementation in one source file, while the
    # editor and exported packages still receive self-contained JavaScript.
    # An edited/imported package already containing the helper needs no prefix.
    if "/* @memu-shared:benchmarks */" in row["js"] and "/* @memu-inlined:benchmarks */" not in row["js"]:
        library = _text_file(builtin_dir() / "benchmarks", "data.js")
        if not library.strip():
            raise ValueError("Общий код бенчмарков отсутствует / Shared benchmark code is missing")
        row["js"] = _clean_code("/* @memu-inlined:benchmarks */\n" + library + "\n" + row["js"], "JS")
    # Round 30: the token estimate MemuAI itself shows (pgstatic/tokens.js), inlined where the marker stands
    # (after the module's "use strict", which must stay first) and wrapped so the library stays private.
    marker = "/* @memu-shared:tokens */"
    if marker in row["js"] and "/* @memu-inlined:tokens */" not in row["js"]:
        library = _text_file(builtin_dir().parent / "pgstatic", "tokens.js")
        if not library.strip():
            raise ValueError("Общая оценка токенов отсутствует / Shared token estimate is missing")
        row["js"] = _clean_code(row["js"].replace(marker, "/* @memu-inlined:tokens */\nvar MemuTokens = (function () {\n"
                                                  + library + "\nreturn MemuTokens;\n})();", 1), "JS")
    # Round 33: a big built-in module keeps a self-contained feature in its own file — «/* @memu-part:<file>.js */»
    # in main.js is replaced by that file of the same package (the override copy first, else the shipped one), so
    # the editor and exported packages still get one self-contained main.js (the marker becomes «@memu-inlined-part»).
    def _inline_part(match: "re.Match") -> str:
        name = match.group(1)
        text = _text_file(folder, name)
        if not text.strip():
            text = _text_file(builtin_dir() / module_id, name) if builtin else ""
        if not text.strip():
            raise ValueError(f"Часть модуля {name} отсутствует / Module part {name} is missing")
        return f"/* @memu-inlined-part:{name} */\n" + text
    if "/* @memu-part:" in row["js"]:
        row["js"] = _clean_code(_PART_RE.sub(_inline_part, row["js"]), "JS")
    if "/* @memu-part:" in row["css"]:
        row["css"] = _clean_code(_CSS_PART_RE.sub(_inline_part, row["css"]), "CSS")
    translations = _read_i18n(folder)
    if not translations and fallback is not None:
        translations = _read_i18n(fallback)
    row["i18n"] = translations
    row["locales"] = sorted(translations)
    row["folder"] = folder.name
    row["saved"] = load_persistent(root, module_id)
    row["missing"] = not (row["html"] or row["js"])
    return row


def module_exists(root: Path, module_id: str) -> bool:
    return any(row["id"] == module_id for row in list_modules(root))


# --------------------------------------------------------------------------
# Переключатели, лаунчер, порядок
# --------------------------------------------------------------------------

def configure_module(root: Path, module_id: str, *, enabled: Any = None,
                     hidden: Any = None, slot: Any = None, order: Any = None,
                     resizable: Any = None) -> Dict[str, Any]:
    module_id = _safe_id(module_id)
    if not module_exists(root, module_id):
        raise KeyError(module_id)
    if slot is not None and str(slot) not in LAUNCHER_SLOTS:
        raise ValueError("Неизвестный слот лаунчера")

    def change(row: Dict[str, Any]) -> None:
        launcher = dict(row.get("launcher") or {}) if isinstance(row.get("launcher"), dict) else {}
        if enabled is not None:
            row["enabled"] = bool(enabled)
        if hidden is not None:
            launcher["hidden"] = bool(hidden)
        if slot is not None:
            launcher["slot"] = str(slot)
        if order is not None:
            launcher["order"] = _clamp_int(order, 0, 0, 9999)
        if resizable is not None:
            row["resizable"] = bool(resizable)
        row["launcher"] = launcher

    _mutate_state(root, module_id, change)
    return next(row for row in list_modules(root) if row["id"] == module_id)


def reorder_modules(root: Path, order: List[str]) -> List[Dict[str, Any]]:
    """Apply the launcher order as it appears in the interface.

    The listed ids take positions 0..n-1. Anything not listed keeps its current
    place after them, so a partial drag never makes a module jump to the top.
    """
    rows = list_modules(root)
    known = {row["id"] for row in rows}
    listed = [str(item) for item in (order or []) if str(item) in known]
    seen: set = set()
    sequence: List[str] = []
    for module_id in listed:
        if module_id not in seen:
            seen.add(module_id)
            sequence.append(module_id)
    for row in rows:
        if row["id"] not in seen:
            sequence.append(row["id"])
    for index, module_id in enumerate(sequence):
        _mutate_state(root, module_id, lambda row, value=index: row.update(
            launcher={**(row.get("launcher") if isinstance(row.get("launcher"), dict) else {}),
                      "order": value}))
    return list_modules(root)


# --------------------------------------------------------------------------
# Окно
# --------------------------------------------------------------------------

def window_policy(manifest: Dict[str, Any], user_box: Optional[Dict[str, Any]] = None,
                  *, resizable: Optional[bool] = None) -> Dict[str, Any]:
    """Resolve the window the host should open.

    Every window can be dragged. Whether it can be resized is the person's
    choice (``resizable``, on by default); the manifest's ``fixed`` and
    ``size_mode: authored`` only decide which size the window *opens* with.
    A position is remembered only after the person actually moved the window
    (``moved``): otherwise the window opens centred in MemuAI.
    """
    window = normalize_window(manifest.get("window"))
    fixed = window["fixed"]
    authored_w, authored_h = window["default_width"], window["default_height"]
    # Выбор человека (оверлей) главнее; без него — флаг «Можно менять размер»
    # из манифеста. Раньше флаг манифеста ни на что не влиял.
    can_resize = bool(window.get("resizable", True)) if resizable is None else bool(resizable)
    prefer_user = window["size_mode"] != "authored" and not fixed
    source = "authored"
    width, height = authored_w, authored_h
    box = user_box if isinstance(user_box, dict) else {}
    if can_resize and (prefer_user or box.get("resized")) and box.get("width"):
        width = _clamp_int(box.get("width"), authored_w, window["min_width"], window["max_width"])
        height = _clamp_int(box.get("height"), authored_h, window["min_height"], window["max_height"])
        source = "user"
    draggable = True
    moved = bool(box.get("moved"))
    x = _clamp_int(box.get("x"), -1, -1, 6000) if moved else -1
    y = _clamp_int(box.get("y"), -1, -1, 6000) if moved else -1
    return {
        "width": width,
        "height": height,
        "min_width": window["min_width"],
        "min_height": window["min_height"],
        "max_width": window["max_width"],
        "max_height": window["max_height"],
        "x": x,
        "y": y,
        "fixed": fixed,
        "resizable": can_resize,
        "draggable": draggable,
        "size_source": source,
        # Несдвинутое окно всегда по центру MemuAI, даже если автор не просил.
        "center": not (x >= 0 and y >= 0),
        "fullscreen": False,
        "fullscreen_allowed": window["fullscreen_allowed"],
    }


def resolve_window(root: Path, module_id: str) -> Dict[str, Any]:
    package = load_package(root, module_id)
    overlay = _overlay(load_state(root), module_id)
    geometry = overlay.get("geometry") if isinstance(overlay.get("geometry"), dict) else None
    policy = window_policy(package, geometry,
                           resizable=None if "resizable" not in overlay else overlay.get("resizable") is not False)
    policy["fullscreen"] = bool(overlay.get("fullscreen")) and policy["fullscreen_allowed"]
    return policy


# Round 31: one-time hints of the module windows («how to close / minimize»).
# Stored next to state.json (not in it: _mutate_state rewrites state.json as
# {"modules": ...}) and not in localStorage, which WebView2 wipes on restart.
UI_HINTS = ("window", "minimize")


def load_ui_hints(root: Path) -> Dict[str, Any]:
    data = _read_json(Path(root) / "ui.json")
    seen = data.get("seen") if isinstance(data.get("seen"), list) else []
    return {"seen": [name for name in UI_HINTS if name in seen]}


def mark_ui_hint(root: Path, name: str, seen: bool = True) -> Dict[str, Any]:
    name = str(name or "")
    if name not in UI_HINTS:
        raise ValueError("Unknown hint / Неизвестная подсказка")
    Path(root).mkdir(parents=True, exist_ok=True)

    def apply(raw: Any) -> Tuple[bool, Any]:
        data = raw if isinstance(raw, dict) else {}
        current = data.get("seen") if isinstance(data.get("seen"), list) else []
        names = [item for item in UI_HINTS if item in current and (seen or item != name)]
        if seen and name not in names:
            names.append(name)
        return True, {**data, "seen": names}

    mutate_json(Path(root) / "ui.json", {}, apply, backup=False,
                validator=lambda value: isinstance(value, dict), repair_invalid=True)
    return load_ui_hints(root)


def remember_window(root: Path, module_id: str, box: Dict[str, Any]) -> Dict[str, Any]:
    module_id = _safe_id(module_id)
    policy = resolve_window(root, module_id)
    keep_size = bool(box.get("keep_size"))
    previous = _overlay(load_state(root), module_id).get("geometry")
    previous = previous if isinstance(previous, dict) else {}
    if keep_size and previous:
        # Хост подогнал окно под маленький экран. Это не выбор человека,
        # поэтому запомненный размер остаётся прежним.
        width = _clamp_int(previous.get("width"), policy["width"],
                           policy["min_width"], policy["max_width"])
        height = _clamp_int(previous.get("height"), policy["height"],
                            policy["min_height"], policy["max_height"])
    else:
        width = _clamp_int(box.get("width"), policy["width"], policy["min_width"], policy["max_width"])
        height = _clamp_int(box.get("height"), policy["height"], policy["min_height"], policy["max_height"])
    # -1 значит «по центру». Раньше -1 обрезалось до 0, и окно, ни разу не
    # сдвинутое человеком, в следующий раз открывалось в левом верхнем углу.
    moved = bool(box.get("moved")) or bool(previous.get("moved") and "moved" not in box)
    resized = bool(box.get("resized")) or bool(previous.get("resized"))
    geometry = {
        "width": width,
        "height": height,
        "x": _clamp_int(box.get("x"), -1, -1, 6000) if moved else -1,
        "y": _clamp_int(box.get("y"), -1, -1, 6000) if moved else -1,
        "moved": moved and _clamp_int(box.get("x"), -1, -1, 6000) >= 0,
        "resized": resized,
    }

    def change(row: Dict[str, Any]) -> None:
        row["geometry"] = geometry
        row["fullscreen"] = bool(box.get("fullscreen"))

    _mutate_state(root, module_id, change)
    return resolve_window(root, module_id)


def mark_open(root: Path, module_id: str, opened: bool) -> None:
    module_id = _safe_id(module_id)
    _mutate_state(root, module_id, lambda row: row.update(open=bool(opened)))


# --------------------------------------------------------------------------
# Сохранённые поля модуля
# --------------------------------------------------------------------------

def _persist_keys(root: Path, module_id: str) -> List[str]:
    owner = Path(root) / ("overrides" if module_id in BUILTIN_ORDER else "custom") / module_id
    with _package_transaction(owner):
        folder = _package_dir(root, module_id)
        if folder is None:
            return []
        manifest = normalize_manifest(_manifest_file(folder), module_id=module_id)
        return list(manifest["persist"])


def load_persistent(root: Path, module_id: str) -> Dict[str, Any]:
    module_id = _safe_id(module_id)
    keys = set(_persist_keys(root, module_id))
    raw = _read_json(Path(root) / "persist" / f"{module_id}.json")
    return {key: raw[key] for key in raw if key in keys}


def save_persistent(root: Path, module_id: str, values: Dict[str, Any]) -> Dict[str, Any]:
    module_id = _safe_id(module_id)
    if not isinstance(values, dict):
        raise ValueError("Поля модуля должны быть объектом")
    keys = set(_persist_keys(root, module_id))
    path = Path(root) / "persist" / f"{module_id}.json"
    updates = {}
    too_large = []
    for key, value in list(values.items())[:200]:
        name = str(key)[:40]
        # Точное совпадение: усечение кандидата не должно давать доступ к
        # другому, объявленному ключу.
        if name in {str(known)[:40] for known in keys}:
            encoded = json.dumps(value, ensure_ascii=False)
            if len(encoded) > _TEXT_LIMIT:
                too_large.append(name)
                continue
            updates[name] = value

    def apply(raw: Dict[str, Any]) -> Tuple[bool, Any]:
        # Partial saves can arrive from different module windows or processes.
        # Read the latest allowed fields and merge while holding the file lock.
        current = {key: raw[key] for key in raw if key in keys}
        current.update(updates)
        return True, current

    current = mutate_json(path, {}, apply, backup=False,
                          validator=lambda value: isinstance(value, dict))
    if too_large:
        # Остальные поля сохранены, но молча оставить старое значение нельзя:
        # модуль решил бы, что сохранил, и после перезапуска получил бы старое.
        raise ValueError(f"Поле {', '.join(too_large)} длиннее {_TEXT_LIMIT} символов и не сохранено")
    return current


def clear_persistent(root: Path, module_id: str) -> None:
    module_id = _safe_id(module_id)
    path = Path(root) / "persist" / f"{module_id}.json"
    if path.is_file():
        path.unlink()


# --------------------------------------------------------------------------
# Создание, правка, удаление, перенос
# --------------------------------------------------------------------------

def _clean_code(value: Any, label: str) -> str:
    text = str(value or "")
    if len(text) > _CODE_LIMIT:
        raise ValueError(f"{label} длиннее {_CODE_LIMIT} символов")
    return text


def _write_package(folder: Path, manifest: Dict[str, Any], spec: Dict[str, Any]) -> None:
    with _package_transaction(Path(folder)):
        _write_package_locked(Path(folder), manifest, spec)


def _write_package_locked(folder: Path, manifest: Dict[str, Any], spec: Dict[str, Any]) -> None:
    """Пишет пакет модуля целиком или никак.

    Сначала проверяется всё (размеры, переводы), потом пишется. Раньше
    manifest.json успевал записаться до ошибки «HTML длиннее…», и оставался
    полусозданный модуль или наполовину сохранённый код.
    """
    html = _clean_code(spec.get("html"), "HTML")
    css = _clean_code(spec.get("css"), "CSS")
    js = _clean_code(spec.get("js"), "JS")
    translations = normalize_i18n(spec.get("i18n"))
    i18n_text = ""
    if translations:
        i18n_text = json.dumps(translations, ensure_ascii=False, indent=2)
        if len(i18n_text) > _CODE_LIMIT:
            raise ValueError(f"Переводы длиннее {_CODE_LIMIT} символов")
    folder.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{folder.name}.staging-", dir=str(folder.parent)))
    previous = folder.parent / f".{folder.name}.previous-{uuid.uuid4().hex}"
    moved = False
    try:
        if folder.exists():
            _reject_package_links(folder)
            # Keep assets and JSON backup history supplied by the person.
            shutil.copytree(folder, staging, dirs_exist_ok=True, symlinks=True)
            _reject_package_links(staging)
        for name, text in (("index.html", html), ("style.css", css), ("main.js", js)):
            (staging / name).unlink(missing_ok=True)
            (staging / name).write_text(text, encoding="utf-8")
        if i18n_text:
            (staging / "i18n.json").unlink(missing_ok=True)
            (staging / "i18n.json").write_text(i18n_text + "\n", encoding="utf-8")
        else:
            (staging / "i18n.json").unlink(missing_ok=True)
        _write_json(staging / "manifest.json", manifest, backup=True)
        if folder.exists():
            os.replace(folder, previous)
            moved = True
        try:
            os.replace(staging, folder)
        except BaseException:
            if moved:
                # The live path is empty: rollback restores every old file,
                # including unknown assets, rather than mixing two versions.
                os.replace(previous, folder)
                moved = False
            raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    if moved:
        shutil.rmtree(previous, ignore_errors=True)


def _reject_package_links(folder: Path) -> None:
    """A package snapshot must not follow links into another user's files."""
    def linked(path: Path) -> bool:
        return is_link(path)

    if linked(folder):
        raise ValueError("Пакет модуля содержит файловую ссылку или junction")
    for parent, directories, files in os.walk(folder, followlinks=False):
        for name in directories + files:
            if linked(Path(parent) / name):
                raise ValueError("Пакет модуля содержит файловую ссылку или junction")


_RESERVED_IDS = re.compile(r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])$")


def validate_id(module_id: Any) -> str:
    value = str(module_id or "").strip()
    if value != value.lower() or not _ID_RE.match(value):
        raise ValueError("Идентификатор: латиница в нижнем регистре, цифры и дефис, до 41 символа, начинается с буквы")
    if _RESERVED_IDS.match(value):
        # con, nul, com1… в Windows — устройства, а не папки.
        raise ValueError("Этот идентификатор зарезервирован Windows (con, nul, com1…)")
    return value


def create_module(root: Path, spec: Dict[str, Any]) -> Dict[str, Any]:
    module_id = validate_id(spec.get("id"))
    if module_id in BUILTIN_ORDER:
        raise ValueError("Этот идентификатор занят встроенным модулем")
    folder = Path(root) / "custom" / module_id
    with _package_transaction(folder):
        if folder.exists():
            raise ValueError("Модуль с таким идентификатором уже есть")
        manifest = normalize_manifest(spec, module_id=module_id)
        manifest["enabled"] = False
        manifest["created"] = int(time.time())
        _write_package(folder, manifest, spec)
        return configure_module(root, module_id, enabled=False,
                                hidden=bool((spec.get("launcher") or {}).get("hidden", False)),
                                slot=manifest["launcher"]["slot"])


def update_code(root: Path, module_id: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    module_id = validate_id(module_id)
    folder = Path(root) / ("overrides" if module_id in BUILTIN_ORDER else "custom") / module_id
    with _package_transaction(folder):
        return _update_code_locked(root, module_id, spec)


def _update_code_locked(root: Path, module_id: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    module_id = validate_id(module_id)
    if module_id in BUILTIN_ORDER:
        if _removed(root, module_id):
            raise KeyError(module_id)
        folder = Path(root) / "overrides" / module_id
        base_manifest = normalize_manifest(_manifest_file(builtin_dir() / module_id), module_id=module_id)
        existing = _read_json(folder / "manifest.json") or base_manifest
        current_code = {
            "html": _text_file(folder, "index.html") or _text_file(builtin_dir() / module_id, "index.html"),
            "css": _text_file(folder, "style.css") or _text_file(builtin_dir() / module_id, "style.css"),
            "js": _text_file(folder, "main.js") or _text_file(builtin_dir() / module_id, "main.js"),
            "i18n": _read_i18n(folder) or _read_i18n(builtin_dir() / module_id),
        }
        # Правки человека поверх поставки, а не наоборот: при обратном порядке
        # каждое сохранение кода возвращало заводские название, окно и права.
        manifest = normalize_manifest(spec, module_id=module_id, base={**base_manifest, **existing})
    else:
        folder = Path(root) / "custom" / module_id
        if not folder.is_dir():
            raise KeyError(module_id)
        existing = _manifest_file(folder)
        current_code = {
            "html": _text_file(folder, "index.html"),
            "css": _text_file(folder, "style.css"),
            "js": _text_file(folder, "main.js"),
            "i18n": _read_i18n(folder),
        }
        manifest = normalize_manifest(spec, module_id=module_id, base=existing)
    code: Dict[str, Any] = {key: _clean_code(spec.get(key, current_code[key]), key.upper())
                            for key in ("html", "css", "js")}
    code["i18n"] = normalize_i18n(spec["i18n"]) if "i18n" in spec else current_code["i18n"]
    _write_package(folder, manifest, code)
    if module_id in BUILTIN_ORDER:
        _mutate_state(root, module_id, lambda row: row.update(edited=True))
    return next(row for row in list_modules(root) if row["id"] == module_id)


def duplicate_module(root: Path, module_id: str, new_id: str, title: str = "") -> Dict[str, Any]:
    module_id = _safe_id(module_id)
    package = load_package(root, module_id)
    spec = {
        "id": validate_id(new_id),
        "title": title or f"{package['title']} 2",
        "title_en": title or f"{package.get('title_en') or package['title']} 2",
        "summary": package.get("summary", ""), "summary_en": package.get("summary_en", ""),
        "icon": _portable_icon(root, module_id, package), "accent": package.get("accent", ""),
        "icon_bg": package.get("icon_bg") or [],
        "window": package.get("window") or {}, "persist": package.get("persist") or [],
        "permissions": package.get("permissions") or [], "bridge": package.get("bridge") or {},
        "launcher": {"hidden": False, "slot": (package.get("launcher") or {}).get("slot", "sidebar")},
        "html": package.get("html", ""), "css": package.get("css", ""), "js": package.get("js", ""),
        "i18n": package.get("i18n") or {}, "default_locale": package.get("default_locale") or "ru",
        # Копия — та же версия и тот же автор, а не «1.0» без автора.
        "version": package.get("version") or "1.0", "author": package.get("author") or "",
    }
    return create_module(root, spec)


def reset_override(root: Path, module_id: str) -> Dict[str, Any]:
    """Throw away a person's edited copy of a built-in package."""
    module_id = _safe_id(module_id)
    if module_id not in BUILTIN_ORDER:
        raise ValueError("Это не встроенный модуль")
    if _removed(root, module_id):
        raise KeyError(module_id)
    folder = Path(root) / "overrides" / module_id
    if folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)
    _mutate_state(root, module_id, lambda row: row.pop("edited", None))
    return next(row for row in list_modules(root) if row["id"] == module_id)


def _removed(root: Path, module_id: str) -> bool:
    return bool(_overlay(load_state(root), module_id).get("removed"))


def removed_builtins(root: Path) -> List[Dict[str, Any]]:
    """Built-in modules the person removed; they can be brought back."""
    state = load_state(root)
    rows = []
    for manifest in builtin_manifests(None):
        if _overlay(state, manifest["id"]).get("removed"):
            rows.append({"id": manifest["id"], "title": manifest["title"],
                         "title_en": manifest["title_en"], "icon": _glyph(manifest)})
    return rows


def restore_builtin(root: Path, module_id: str) -> Dict[str, Any]:
    module_id = _safe_id(module_id)
    if module_id not in BUILTIN_ORDER:
        raise ValueError("Это не встроенный модуль")
    _mutate_state(root, module_id, lambda row: (row.clear(), row.update(enabled=False)))
    return next(row for row in list_modules(root) if row["id"] == module_id)


def delete_module(root: Path, module_id: str) -> None:
    module_id = validate_id(module_id)
    if module_id in BUILTIN_ORDER:
        # Встроенный модуль удаляется для человека: пропадают его правки кода,
        # сохранённые поля и настройки, а сам пакет остаётся в установке, и
        # модуль можно вернуть из списка «Удалённые встроенные».
        if _removed(root, module_id):
            raise KeyError(module_id)
        shutil.rmtree(Path(root) / "overrides" / module_id, ignore_errors=True)
        clear_persistent(root, module_id)
        _mutate_state(root, module_id, lambda row: (row.clear(), row.update(removed=True)))
        return
    folder = Path(root) / "custom" / module_id
    if not folder.is_dir():
        raise KeyError(module_id)
    shutil.rmtree(folder, ignore_errors=True)
    def change(raw: Any) -> Tuple[bool, Any]:
        data = raw if isinstance(raw, dict) else {}
        rows = data.get("modules")
        if isinstance(rows, dict):
            rows.pop(module_id, None)
        return True, {"modules": rows if isinstance(rows, dict) else {}}
    mutate_json(Path(root) / "state.json", {"modules": {}}, change, backup=False,
                validator=lambda value: isinstance(value, dict), repair_invalid=True)
    clear_persistent(root, module_id)
    from .module_icons import reset_user_icon
    reset_user_icon(root, module_id)  # Round 39: the person's icon goes with the module


# --------------------------------------------------------------------------
# Перенос модулей файлом
# --------------------------------------------------------------------------

PACKAGE_FORMAT = "memu-module"
PACKAGE_VERSION = 2


def export_package(root: Path, module_id: str) -> Dict[str, Any]:
    module_id = _safe_id(module_id)
    package = load_package(root, module_id)
    contributes = _manifest_contributes(root, module_id)
    return {
        "format": PACKAGE_FORMAT,
        "version": PACKAGE_VERSION,
        "exported": int(time.time()),
        "manifest": {
            "id": package["id"], "title": package["title"], "title_en": package["title_en"],
            "summary": package["summary"], "summary_en": package["summary_en"],
            "version": package["version"], "author": package["author"],
            "default_locale": package.get("default_locale") or "ru",
            "icon": _portable_icon(root, module_id, package), "accent": package["accent"],
            **({"icon_bg": package["icon_bg"]} if package.get("icon_bg") else {}),
            "window": package["window"], "persist": package["persist"],
            "permissions": package["permissions"], "bridge": package["bridge"],
        },
        "files": {
            "index.html": package["html"],
            "style.css": package["css"],
            "main.js": package["js"],
            "i18n.json": package.get("i18n") or {},
            # Round 38: panel pages / scripts / styles and the skill a «contributes» block names.
            **_contributed_files(root, module_id, contributes),
        },
        **({"contributes": contributes} if contributes else {}),
    }


def _portable_icon(root: Path, module_id: str, package: Dict[str, Any]) -> str:
    """Round 39: the manifest icon for a copy/export — a package file becomes inline SVG / a data URL."""
    from .module_icons import DATA_LIMIT, INLINE_LIMIT, IconError, icon_bytes, icon_kind
    raw = str(package.get("icon_src") or package.get("icon") or "")
    if icon_kind(raw) != "file":
        return raw
    try:
        body, media = icon_bytes(root, module_id, own=True)  # the manifest's own icon, not the person's replacement
    except (KeyError, ValueError, OSError, IconError):
        return package.get("icon") or ""
    if media == "image/svg+xml" and len(body) <= INLINE_LIMIT:
        return body.decode("utf-8")
    import base64
    data = "data:" + media + ";base64," + base64.b64encode(body).decode("ascii")
    return data if len(data) <= DATA_LIMIT else (package.get("icon") or "")


def _manifest_contributes(root: Path, module_id: str) -> Dict[str, Any]:
    """Round 38: the «contributes» block of the module's manifest (export carries it with its files)."""
    for folder in (_package_dir(root, module_id), builtin_dir() / module_id if module_id in BUILTIN_ORDER else None):
        if folder is not None and (folder / "manifest.json").is_file():
            value = _read_json(folder / "manifest.json").get("contributes")
            return value if isinstance(value, dict) else {}
    return {}


def _contributed_files(root: Path, module_id: str, contributes: Dict[str, Any]) -> Dict[str, str]:
    if not contributes:
        return {}
    from .module_integration import package_files
    folder = _package_dir(root, module_id)
    files = package_files(folder, contributes) if folder is not None else {}
    if module_id in BUILTIN_ORDER:
        files = {**package_files(builtin_dir() / module_id, contributes), **files}
    return files


def import_package(root: Path, payload: Dict[str, Any], *, new_id: str = "",
                   title: str = "") -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Файл модуля должен быть объектом")
    body = payload.get("manifest") if isinstance(payload.get("manifest"), dict) else payload
    files = payload.get("files") if isinstance(payload.get("files"), dict) else {}
    if not body:
        raise ValueError("В файле нет описания модуля")
    source_id = str(body.get("id") or "").strip().lower()
    module_id = validate_id(new_id or source_id)
    if module_id in BUILTIN_ORDER:
        raise ValueError("Идентификатор занят встроенным модулем")
    if (Path(root) / "custom" / module_id).is_dir():
        raise ValueError("Модуль с таким идентификатором уже есть — укажите другой")
    spec = dict(body)
    spec["id"] = module_id
    if title:
        spec["title"] = title
        spec["title_en"] = title  # иначе в английском интерфейсе видно старое имя
    spec["html"] = str(files.get("index.html", body.get("html", "")))[:_CODE_LIMIT]
    spec["css"] = str(files.get("style.css", body.get("css", "")))[:_CODE_LIMIT]
    spec["js"] = str(files.get("main.js", body.get("js", "")))[:_CODE_LIMIT]
    spec["i18n"] = normalize_i18n(files.get("i18n.json", body.get("i18n", {})))
    spec.pop("enabled", None)
    if not (spec["html"] or spec["js"]):
        raise ValueError("В файле нет кода модуля")
    if isinstance(payload.get("contributes"), dict) and "contributes" not in spec:
        spec["contributes"] = payload["contributes"]
    created = create_module(root, spec)
    if spec.get("contributes"):
        # Round 38: the files of its panels and skill (only names «contributes» declares; module_integration.py).
        from .module_integration import write_package_files
        write_package_files(Path(root) / "custom" / module_id, spec["contributes"], files)
    return created


# --------------------------------------------------------------------------
# Мост к модели
# --------------------------------------------------------------------------

def _safe_images(images: Any, limit: int = 8) -> List[str]:
    # Сначала отбрасываем мусор, потом режем по лимиту: иначе чужие ссылки в
    # начале списка съедали место настоящих изображений.
    rows = images if isinstance(images, list) else []
    return [item for item in rows
            if isinstance(item, str) and item.startswith("data:image/")][:limit]


def _attr_document_name(name: Any) -> str:
    """A document name safe to sit inside ``<file name="...">``.

    The chat prompt format uses that tag as a delimiter. A name containing a
    quote could close the attribute and open a fake tag, so the model would read
    module-supplied text as a genuinely attached document.
    """
    cleaned = re.sub(r'["<>&\r\n\t]+', " ", str(name or "")).strip(" .")
    return cleaned[:120] or "file"


def _safe_documents(documents: Any, limit: int = 8) -> List[Dict[str, str]]:
    output: List[Dict[str, str]] = []
    for item in (documents or [])[:limit]:
        if not isinstance(item, dict):
            continue
        try:
            size = len(str(item.get("text") or ""))
        except Exception:
            size = 0
        output.append({
            "name": _attr_document_name(item.get("name")),
            "text": str(item.get("text") or "")[:min(max(size, 0), 400_000)],
        })
    return output


def build_request(*, model: str = "", instructions: str = "", keep_in_chat: bool = False,
                  text: str = "", images: Optional[List[Any]] = None,
                  documents: Optional[List[Any]] = None,
                  history: Optional[List[Any]] = None,
                  thinking: bool = False, web_search: bool = False,
                  temperature: Any = None) -> Dict[str, Any]:
    """Build the body a module sends through the MemuAI Proxy bridge."""
    safe_images = _safe_images(images)
    safe_documents = _safe_documents(documents)
    instruction = str(instructions or "")
    parts = [str(text or "")]
    for document in safe_documents:
        # Имя документа не должно уметь закрыть атрибут и подделать вид
        # «настоящего» вложения в промпте: кавычки и угловые скобки убираем.
        safe_name = _attr_document_name(document["name"])
        parts.append(f'\n<file name="{safe_name}">\n{document["text"]}\n</file>')
    user_text = "\n".join(part for part in parts if part).strip()
    if safe_images:
        content: Any = [{"type": "text", "text": user_text or " "}]
        content.extend({"type": "image_url", "image_url": {"url": url}} for url in safe_images)
    else:
        content = user_text
    messages: List[Dict[str, Any]] = []
    if instruction:
        messages.append({"role": "system", "content": instruction})
    rows = [item for item in (history or []) if isinstance(item, dict)]
    # Пустой ответ-заготовка в конце (шаблон мини-чата кладёт его перед
    # запросом) и последняя реплика пользователя, равная text, — это и есть
    # текущий запрос: второй раз его отправлять нельзя.
    while rows and not str(rows[-1].get("content") or "").strip():
        rows.pop()
    if rows and str(rows[-1].get("role")) != "assistant" and \
            str(rows[-1].get("content") or "").strip() == str(text or "").strip():
        rows.pop()
    for item in rows[-40:]:
        role = "assistant" if str(item.get("role")) == "assistant" else "user"
        body = str(item.get("content") or "").strip()
        if not body:
            continue
        messages.append({"role": role, "content": body[:200_000]})
    messages.append({"role": "user", "content": content})
    request: Dict[str, Any] = {
        "model": str(model or "")[:300],
        "instructions": instruction,
        "keep_in_chat": bool(keep_in_chat),
        "text": str(text or ""),
        "images": safe_images,
        "documents": safe_documents,
        "thinking": bool(thinking),
        "web_search": bool(web_search),
        "messages": messages,
    }
    try:
        if temperature is not None:
            request["temperature"] = max(0.0, min(2.0, float(temperature)))
    except (TypeError, ValueError):
        pass
    return request


def design_handoff(*, prompt: str = "", images: Optional[List[Any]] = None,
                   code: str = "", filename: str = "index.html",
                   files: Optional[List[Any]] = None) -> Dict[str, Any]:
    """Bundle a design prompt, its images, and the source for another AI."""
    safe_images = _safe_images(images, 12)
    bundle: List[Dict[str, str]] = []
    for item in (files or [])[:20]:
        if not isinstance(item, dict):
            continue
        name = _bundle_name(item.get("name"), "file.txt")
        bundle.append({"name": name, "content": str(item.get("content") or "")[:_CODE_LIMIT]})
    if not bundle:
        bundle.append({"name": _bundle_name(filename, "index.html"), "content": str(code or "")})
    return {
        "prompt": str(prompt or ""),
        "images": safe_images,
        "files": bundle,
        "readme": _handoff_readme(str(prompt or ""), len(safe_images), bundle),
    }


def _bundle_name(value: Any, fallback: str) -> str:
    """A file name without path separators; a name of only junk gets the fallback."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", str(value or "")).strip(" ._")[:80]
    return name or fallback


def _handoff_readme(prompt: str, image_count: int, files: List[Dict[str, str]]) -> str:
    listing = "\n".join(f"- {item['name']}" for item in files)
    return (
        "# MemuAI Design — пакет для другой ИИ\n\n"
        "В этой папке лежит готовый дизайн, его исходники и запрос, по которому он сделан.\n\n"
        "## Что делать дальше\n\n"
        "1. Откройте файл `index.html` — это готовая страница.\n"
        "2. Передайте другой ИИ промпт из `prompt.txt`, изображения и код.\n"
        "3. Попросите продолжить, не меняя уже принятую дизайн-систему.\n\n"
        f"## Изображений в пакете: {image_count}\n\n"
        f"## Файлы\n\n{listing}\n\n"
        "## Исходный запрос\n\n"
        f"{prompt}\n"
    )


# --------------------------------------------------------------------------
# Документ модуля: оформление, среда, мост
# --------------------------------------------------------------------------

#: Общий слой оформления для страницы модуля. Модуль может его не
#: использовать вовсе, но тогда он не будет похож на MemuAI. Классы с
#: префиксом ``mu-`` никогда не пересекаются со стилями самого модуля.
#:
#: Токены ниже повторяют appearance.css. Страница модуля живёт в отдельном
#: документе и не наследует переменные родителя, поэтому значения обязаны
#: быть в самом документе: без них модуль получает прозрачный фон и шрифт
#: по умолчанию — именно так он выглядел «без стилей».

def design_tokens(env: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """Values the module page needs so it matches the MemuAI interface.

    Colours come from app/themes.py, the same catalogue the desktop UI is
    generated from, so a built-in variant or a user theme looks identical
    inside a module on first paint. The open page later receives the host's
    computed values through ``broadcastTheme`` as well.
    """
    from . import themes
    environment = dict(env or {})
    theme = themes.normalize_mode(environment.get("theme"))
    tokens = themes.resolve_tokens(theme, str(environment.get("surface") or ""),
                                   str(environment.get("accent_name") or ""))
    density = str(environment.get("density") or "normal")
    radius = "9px" if density == "compact" else "12px"
    tokens.update({
        "--radius": radius,
        "--radius-sm": "8px",
        "--radius-md": radius,
        "--radius-lg": "16px",
        "--sans": '"Segoe UI Variable Text", "Segoe UI", -apple-system, BlinkMacSystemFont, system-ui, sans-serif',
        "--serif": '"Tiempos Headline", "Charter", "Iowan Old Style", Georgia, serif',
        "--mono": '"Cascadia Code", Consolas, monospace',
    })
    try:
        scale = int(environment.get("font_scale") or 100)
    except (TypeError, ValueError):
        scale = 100
    tokens["--font-scale"] = str(max(85, min(125, scale)))
    return tokens


def token_css(env: Optional[Dict[str, Any]] = None) -> str:
    tokens = design_tokens(env)
    body = ";".join(f"{name}:{value}" for name, value in tokens.items() if value)
    return f":root{{{body}}}\n"


BASE_CSS = """
*,*::before,*::after{box-sizing:border-box}
html,body{margin:0;height:100%}
body{background:var(--bg);color:var(--text);font-family:var(--sans);
  font-size:calc(14.5px / 100 * var(--font-scale,100));line-height:1.5;
  -webkit-font-smoothing:antialiased;overflow:hidden}
[hidden]{display:none!important}
*{scrollbar-width:thin;scrollbar-color:var(--border) transparent}
[data-memu-host-hidden]{display:none!important}
*::-webkit-scrollbar{width:10px;height:10px}
*::-webkit-scrollbar-thumb{background:var(--border);border-radius:6px;border:3px solid transparent;background-clip:content-box}
*::-webkit-scrollbar-thumb:hover{background:var(--control-border);background-clip:content-box}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
h1,h2,h3,h4{margin:0 0 8px;font-weight:600;letter-spacing:-.01em}
h1{font-size:1.5em}h2{font-size:1.25em}h3{font-size:1.08em}
p{margin:0 0 10px}
a{color:var(--accent)}
code,kbd,pre{font-family:var(--mono);font-size:.92em}
pre{background:var(--bg-deep);padding:12px;border-radius:var(--radius-sm);overflow:auto;margin:0 0 10px}
kbd{border:1px solid var(--border);border-bottom-width:2px;border-radius:5px;padding:1px 5px;background:var(--panel-2)}
::selection{background:var(--accent-soft)}
.mu-page{display:flex;flex-direction:column;gap:18px;padding:20px;height:100%;overflow:auto;scrollbar-gutter:stable}
.mu-col{display:flex;flex-direction:column;min-height:0}
.mu-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.mu-grow{flex:1;min-width:0}
.mu-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}
.mu-title{font-size:1.28em;font-weight:650;letter-spacing:-.02em}
.mu-sub,.mu-muted{color:var(--muted)}
.mu-sub{font-size:.94em}
.mu-card{border:1px solid var(--border);border-radius:var(--radius-lg);background:var(--panel);padding:18px;
  box-shadow:0 2px 10px color-mix(in srgb,var(--text) 2%,transparent)}
.mu-card>.mu-card{border-radius:var(--radius-sm);background:var(--panel-2)}
.mu-toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:10px 12px;
  border:1px solid var(--border);border-radius:var(--radius);background:var(--panel)}
.mu-field{display:flex;flex-direction:column;gap:5px;font-size:.86em;color:var(--muted)}
.mu-input,.mu-select,.mu-textarea{width:100%;min-height:36px;padding:8px 11px;font:inherit;color:var(--text);
  background:var(--panel-2);border:1px solid var(--border);border-radius:var(--radius-sm);
  transition:border-color .16s,box-shadow .16s;appearance:none}
.mu-select{background-image:linear-gradient(45deg,transparent 50%,var(--muted) 50%),
  linear-gradient(135deg,var(--muted) 50%,transparent 50%);
  background-position:calc(100% - 16px) 50%,calc(100% - 11px) 50%;
  background-size:5px 5px;background-repeat:no-repeat;padding-right:32px}
.mu-textarea{resize:none;min-height:76px;line-height:1.45}
.mu-input:hover,.mu-select:hover,.mu-textarea:hover{border-color:color-mix(in srgb,var(--accent) 55%,var(--border))}
.mu-input:focus,.mu-select:focus,.mu-textarea:focus{outline:none;border-color:var(--accent);
  box-shadow:0 0 0 3px var(--accent-soft)}
.mu-btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;min-height:36px;padding:0 14px;
  font:inherit;font-weight:550;color:var(--text);background:var(--panel-2);border:1px solid var(--border);
  border-radius:var(--radius-sm);cursor:pointer;transition:border-color .16s,background-color .16s,transform .1s}
.mu-btn:hover{border-color:color-mix(in srgb,var(--accent) 60%,var(--border))}
.mu-btn:active{transform:translateY(1px)}
.mu-btn:focus-visible,.mu-tab:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
.mu-btn svg{flex-shrink:0}
.mu-btn:disabled{opacity:.5;cursor:not-allowed;transform:none}
.mu-btn.primary{background:var(--accent);border-color:transparent;color:var(--on-accent)}
.mu-btn.primary:hover{background:color-mix(in srgb,var(--accent) 86%,#000)}
.mu-btn.ghost{background:transparent}
.mu-btn.danger{color:var(--danger);border-color:color-mix(in srgb,var(--danger) 45%,var(--border))}
.mu-btn.small{min-height:30px;padding:0 10px;font-size:.9em}
.mu-tabs{display:flex;flex-shrink:0;gap:4px;padding:4px;border:1px solid var(--border);border-radius:calc(var(--radius) + 2px);
  background:var(--panel-2);align-self:flex-start;max-width:100%;overflow-x:auto;overflow-y:hidden;
  scrollbar-width:none;overscroll-behavior-x:contain}
.mu-tabs::-webkit-scrollbar,.mu-hscroll::-webkit-scrollbar{display:none}
.mu-hscroll{overflow-x:auto;overflow-y:hidden;scrollbar-width:none;overscroll-behavior-x:contain}
.mu-tab{padding:7px 14px;border-radius:var(--radius-sm);border:none;background:transparent;color:var(--muted);
  font:inherit;font-weight:550;cursor:pointer;white-space:nowrap}
.mu-tab:hover{color:var(--text)}
.mu-tab[aria-selected="true"]{background:var(--panel);color:var(--text);box-shadow:0 1px 3px rgba(0,0,0,.12)}
.mu-badge{display:inline-flex;align-items:center;gap:5px;padding:2px 8px;border-radius:999px;
  font-size:.78em;font-weight:600;background:var(--accent-soft);color:var(--accent)}
.mu-note{padding:10px 12px;border-radius:var(--radius-sm);border:1px solid var(--border);
  background:var(--panel-2);color:var(--muted);font-size:.9em}
.mu-note.warn{border-color:color-mix(in srgb,var(--danger) 40%,var(--border));color:var(--danger)}
.mu-empty{display:grid;place-items:center;gap:6px;padding:26px;color:var(--muted);text-align:center;
  border:1px dashed var(--border);border-radius:var(--radius);min-height:120px}
.mu-out{min-height:60px}
.mu-split{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,2fr);gap:12px;min-height:0}
.mu-check{display:flex;align-items:center;gap:8px;font-size:.94em;line-height:1.5;color:var(--text)}
.mu-check input{width:16px;height:16px;accent-color:var(--accent)}
.mu-scroll{overflow:auto;min-height:0;overscroll-behavior:contain}
.mu-spinner{width:16px;height:16px;border:2px solid var(--border);border-top-color:var(--accent);
  border-radius:50%;animation:mu-spin .7s linear infinite;display:inline-block;vertical-align:-3px}
@keyframes mu-spin{to{transform:rotate(360deg)}}
.mu-progress{height:6px;border-radius:3px;background:var(--panel-2);overflow:hidden}
.mu-progress>i{display:block;height:100%;background:var(--accent);width:0;transition:width .3s}
.mu-thumb{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:8px}
.mu-thumb img{width:100%;border-radius:var(--radius-sm);display:block}
.mu-stage{border:1px solid var(--border);border-radius:var(--radius);background:var(--bg-deep);
  display:grid;place-items:center;min-height:200px;overflow:auto;padding:10px}
.mu-stage img,.mu-stage video{max-width:100%;max-height:100%;border-radius:var(--radius-sm)}
.mu-log{display:flex;flex-direction:column;gap:8px;padding:8px}
.mu-msg{padding:9px 12px;border-radius:var(--radius);background:var(--panel-2);max-width:80%;
  white-space:pre-wrap;overflow-wrap:anywhere}
.mu-msg.me{align-self:flex-end;background:var(--accent-soft)}
.mu-composer{display:flex;gap:8px;align-items:flex-end;padding:10px;border-top:1px solid var(--border)}
.mu-composer .mu-textarea{min-height:38px;max-height:160px}
@media (max-width:620px){
  .mu-page{padding:14px;gap:14px}
  .mu-grid,.mu-split{grid-template-columns:1fr}
}
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{animation-duration:.01ms!important;transition-duration:.01ms!important}
}
""".strip()

#: Мост ``Memu``. Инжектируется первым, чтобы код модуля мог им пользоваться.
_MEMU_KEYS_CACHE: Dict[str, str] = {}


def _memu_keys_js() -> str:
    """Round 33 (#8): pgstatic/memu-keys.js inlined into every module document (CSP allows inline scripts only),
    so module shortcuts (Ctrl+S, Ctrl+Z…) work with the Russian layout too."""
    if "js" not in _MEMU_KEYS_CACHE:
        try:
            text = (Path(__file__).resolve().parent / "pgstatic" / "memu-keys.js").read_text(encoding="utf-8")
        except OSError:
            text = ""
        _MEMU_KEYS_CACHE["js"] = text.replace("</", "<\\/")
    return _MEMU_KEYS_CACHE["js"]


BRIDGE_JS = r"""
(function () {
  "use strict";
  var MODULE_ID = __MEMU_MODULE_ID__;
  var pending = Object.create(null);
  var streams = Object.create(null);
  var events = Object.create(null);
  var hostState = __MEMU_ENV__;
  var MANIFEST = __MEMU_MANIFEST__;
  var I18N = __MEMU_I18N__;
  var seq = 0;
  // Номер запроса начинается с нуля при каждой загрузке страницы. Без
  // случайной приставки ответ на запрос до «Перезагрузить» доставался
  // новому запросу с тем же номером.
  var NONCE = Math.random().toString(36).slice(2, 10);
  var TIMEOUTS = {
    request: 300000, stream: 0, save: 20000, models: 20000, image: 900000,
    video: 1800000, speak: 180000, transcribe: 600000, extract: 120000,
    handoff: 60000, download: 180000, notify: 60000, open: 20000, monitor: 180000, music: 700000,
    search: 60000, fetch: 60000, clipboard: 10000, routes: 10000, env: 5000,
    resize: 5000, chat_history: 30000, record: 120000, complete: 0, store: 120000, asset: 60000,
    net_import: 200000, learn_call: 180000
  };

  function callWithId(id, action, payload) {
    return new Promise(function (resolve, reject) {
      var timeout = TIMEOUTS[action] === undefined ? 120000 : TIMEOUTS[action];
      var timer = timeout > 0 ? setTimeout(function () {
        delete pending[id];
        reject(new Error("MemuAI не ответил на «" + action + "» за " + Math.round(timeout / 1000) + " с"));
      }, timeout) : null;
      pending[id] = { resolve: resolve, reject: reject, timer: timer };
      try {
        // from — идентификатор того модуля, чья страница сейчас говорит.
        // Без него хост опирался на «открытый модуль», а фрейм во время
        // перехода успевал отправить отложенное сохранение от прошлой
        // страницы: данные одного модуля попадали в файл другого.
        parent.postMessage({ type: "memu-module", id: id, from: MODULE_ID,
                             action: action, payload: payload || {} }, "*");
      } catch (error) {
        if (timer) clearTimeout(timer);
        delete pending[id];
        reject(error);
      }
    });
  }

  function call(action, payload) {
    seq += 1;
    return callWithId(MODULE_ID + ":" + NONCE + ":" + seq, action, payload);
  }

  function emit(type, data) {
    var list = events[type] || [];
    for (var index = 0; index < list.length; index += 1) {
      try { list[index](data); } catch (error) { if (window.console) console.error(error); }
    }
    var all = events["*"] || [];
    for (var index2 = 0; index2 < all.length; index2 += 1) {
      try { all[index2](type, data); } catch (error) { if (window.console) console.error(error); }
    }
  }

  function applyTheme(tokens) {
    hostState.theme = tokens.theme || hostState.theme;
    hostState.accent_name = tokens.accent_name || hostState.accent_name;
    var root = document.documentElement;
    if (tokens.tokens) {
      Object.keys(tokens.tokens).forEach(function (name) {
        if (tokens.tokens[name]) root.style.setProperty(name, tokens.tokens[name]);
      });
    }
    root.setAttribute("data-theme", hostState.theme || "dark");
    root.setAttribute("data-accent", hostState.accent_name || "terracotta");
    root.setAttribute("data-surface", tokens.surface || hostState.surface || "");
    root.setAttribute("data-density", tokens.density || hostState.density || "normal");
    emit("theme", hostState);
  }

  /* ------------------------------ переводы ------------------------------ */

  var I18N_ATTRS = ["placeholder", "title", "aria-label", "alt", "value", "label"];

  function localeChain(lang) {
    var chain = [];
    function add(code) { if (code && chain.indexOf(code) < 0) chain.push(code); }
    add(lang);
    add(String(lang || "").split("-")[0]);
    add(MANIFEST.default_locale);
    add("en");
    add("ru");
    Object.keys(I18N).forEach(add);
    return chain;
  }

  function lookup(key, lang) {
    var chain = localeChain(lang || hostState.lang);
    for (var index = 0; index < chain.length; index += 1) {
      var table = I18N[chain[index]];
      if (table && Object.prototype.hasOwnProperty.call(table, key)) return table[key];
    }
    return undefined;
  }

  function intlLocale(lang) {
    var code = lang || hostState.lang || "ru";
    return code === "ru" ? "ru-RU" : code === "en" ? "en-US" : code;
  }

  function interpolate(text, vars) {
    return String(text).replace(/\{(\w+)\}/g, function (match, name) {
      return vars && vars[name] !== undefined && vars[name] !== null ? String(vars[name]) : match;
    });
  }

  function translate(key, vars) {
    var value = lookup(String(key));
    if (value === undefined) {
      return vars && vars.fallback !== undefined ? interpolate(vars.fallback, vars) : String(key);
    }
    if (value && typeof value === "object") {
      var count = vars && typeof vars.count === "number" ? vars.count : 0;
      var form = "other";
      try { form = new Intl.PluralRules(intlLocale()).select(count); }
      catch (error) { form = count === 1 ? "one" : "other"; }
      value = value[form] !== undefined ? value[form] : (value.other !== undefined ? value.other : "");
    }
    return interpolate(value, vars);
  }

  function applyTranslations(scope) {
    var root = scope || document;
    if (!root.querySelectorAll) return;
    var nodes = root.querySelectorAll("[data-i18n],[data-i18n-html]," + I18N_ATTRS.map(function (name) {
      return "[data-i18n-" + name + "]";
    }).join(","));
    for (var index = 0; index < nodes.length; index += 1) {
      var node = nodes[index];
      if (node.hasAttribute("data-i18n")) node.textContent = translate(node.getAttribute("data-i18n"));
      if (node.hasAttribute("data-i18n-html")) node.innerHTML = translate(node.getAttribute("data-i18n-html"));
      for (var a = 0; a < I18N_ATTRS.length; a += 1) {
        var attr = "data-i18n-" + I18N_ATTRS[a];
        if (!node.hasAttribute(attr)) continue;
        var text = translate(node.getAttribute(attr));
        if (I18N_ATTRS[a] === "value" && "value" in node) node.value = text;
        else node.setAttribute(I18N_ATTRS[a], text);
      }
    }
    if (root === document) document.documentElement.setAttribute("lang", hostState.lang || "ru");
  }

  function setLanguage(lang) {
    var next = /^[a-z]{2,3}(-[A-Za-z]{2,4})?$/.test(String(lang || "")) ? String(lang) : "ru";
    var previous = hostState.lang;
    if (next === previous) return;
    hostState.lang = next;
    applyTranslations(document);
    emit("language", { lang: next, previous: previous });
  }

  var i18n = {
    t: translate,
    apply: applyTranslations,
    has: function (key) { return lookup(String(key)) !== undefined; },
    // The raw value in one specific locale, without fallback or interpolation.
    get: function (key, lang) {
      var table = I18N[lang || hostState.lang];
      return table && Object.prototype.hasOwnProperty.call(table, key) ? table[key] : undefined;
    },
    locales: function () { return Object.keys(I18N); },
    locale: function () { return intlLocale(); },
    add: function (lang, table) {
      if (!lang || !table || typeof table !== "object") return;
      I18N[lang] = I18N[lang] || {};
      Object.keys(table).forEach(function (key) { I18N[lang][key] = table[key]; });
      applyTranslations(document);
    },
    extend: function (tables) {
      Object.keys(tables || {}).forEach(function (lang) { i18n.add(lang, tables[lang]); });
    },
    number: function (value, options) {
      try { return new Intl.NumberFormat(intlLocale(), options).format(value); } catch (error) { return String(value); }
    },
    date: function (value, options) {
      try { return new Intl.DateTimeFormat(intlLocale(), options).format(value instanceof Date ? value : new Date(value)); }
      catch (error) { return String(value); }
    },
    list: function (items, options) {
      try { return new Intl.ListFormat(intlLocale(), options || { type: "conjunction" }).format(items); }
      catch (error) { return (items || []).join(", "); }
    }
  };

  window.addEventListener("message", function (event) {
    var data = event.data || {};
    if (data.type === "memu-module-result" && data.id && pending[data.id]) {
      var entry = pending[data.id];
      if (entry.timer) clearTimeout(entry.timer);
      delete pending[data.id];
      if (data.error) {
        var problem = new Error(String(data.error));
        problem.detail = data.detail || null;
        entry.reject(problem);
      } else entry.resolve(data.result === undefined ? null : data.result);
      return;
    }
    // Итог потока приходит обычным ответом, когда поток остановлен или упал
    // до первого кадра. Раньше такой ответ искали только среди обычных
    // вызовов, и promise.cancel() оставлял поток висеть навсегда.
    if (data.type === "memu-module-result" && data.id && streams[data.id]) {
      var open = streams[data.id];
      delete streams[data.id];
      if (data.error) {
        if (open.onError) open.onError(String(data.error));
        open.reject(new Error(String(data.error)));
      } else open.resolve(data.result || {});
      return;
    }
    if (data.type === "memu-module-host") {
      if (data.action === "language") setLanguage((data.payload || {}).lang);
      else if (data.action === "theme") {
        applyTheme(data.payload || {});
        if (data.payload && data.payload.lang) setLanguage(data.payload.lang);
      }
      else if (data.action === "host-event") {
        var hostEventName = String(data.payload && data.payload.name || "");
        if (hostEventName === "toolbar") runToolbarClick((data.payload || {}).data || {});
        if (hostEventName === "appbar") applyAppbar((data.payload || {}).data || {});
        emit(hostEventName, (data.payload || {}).data);
      }
      else if (data.action === "reload") location.reload();
      return;
    }
    if (data.type === "memu-module-event" && data.id && streams[data.id]) {
      var stream = streams[data.id];
      if (data.event === "delta") {
        if (stream.onDelta) stream.onDelta(String(data.delta || ""), data);
      } else if (data.event === "reasoning") {
        if (stream.onReasoning) stream.onReasoning(String(data.delta || ""), data);
      } else if (data.event === "usage") {
        if (stream.onUsage) stream.onUsage(data.usage || {}, data);
      } else if (data.event === "status") {
        if (stream.onStatus) stream.onStatus(data.status || "", data);
      } else if (data.event === "done") {
        delete streams[data.id];
        if (stream.onDone) stream.onDone(data.result || {});
        stream.resolve(data.result || {});
      } else if (data.event === "error") {
        delete streams[data.id];
        if (stream.onError) stream.onError(String(data.error || ""));
        stream.reject(new Error(String(data.error || "Ошибка потока")));
      } else if (data.event === "tick") {
        if (stream.onStatus) stream.onStatus("working", data);
      }
    }
  }, false);

  // Esc, который сам модуль не обработал (не вызвал preventDefault) и который
  // нажат вне поля ввода, сообщается окну MemuAI: так развёрнутый лист
  // модуля сворачивается тем же Esc, что и остальные оверлеи MemuAI.
  window.addEventListener("keydown", function (event) {
    if (event.key !== "Escape" || event.repeat || event.altKey || event.ctrlKey || event.metaKey) return;
    var target = event.target;
    if (target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName || ""))) return;
    setTimeout(function () {
      if (event.defaultPrevented || document.querySelector("dialog[open]")) return;
      try { parent.postMessage({ type: "memu-module-key", from: MODULE_ID, key: "Escape" }, "*"); }
      catch (error) { /* окно закрыто */ }
    }, 0);
  }, false);

  // Round 31: window shortcuts work while the focus is inside the module too
  // (Ctrl+W closes the module, Alt+Shift+M minimizes, Alt+Shift+arrows snap).
  // Only combinations with Ctrl/Alt that the module did not handle itself are
  // passed up; MemuAI matches them against its shortcut settings.
  window.addEventListener("keydown", function (event) {
    if (event.repeat || !(event.ctrlKey || event.altKey || event.metaKey)) return;
    if (/^(Control|Alt|Shift|Meta|OS|AltGraph)$/.test(event.key || "")) return;
    setTimeout(function () {
      if (event.defaultPrevented) return;
      try {
        parent.postMessage({ type: "memu-module-key", from: MODULE_ID, key: "combo", combo: {
          key: String(event.key || ""), code: String(event.code || ""), ctrlKey: !!event.ctrlKey,
          altKey: !!event.altKey, shiftKey: !!event.shiftKey, metaKey: !!event.metaKey } }, "*");
      } catch (error) { /* окно закрыто */ }
    }, 0);
  }, false);

  // Round 30: ribbons (tabs, chips, a narrow nav) scroll sideways without a
  // visible bar, so an ordinary mouse wheel scrolls them: a vertical wheel over
  // a box that only scrolls horizontally moves it left/right. A box that can
  // scroll vertically on the way up keeps the wheel.
  window.addEventListener("wheel", function (event) {
    if (event.ctrlKey || event.shiftKey || Math.abs(event.deltaX) >= Math.abs(event.deltaY) || !event.deltaY) return;
    for (var node = event.target; node && node.nodeType === 1 && node !== document.body; node = node.parentElement) {
      var style = getComputedStyle(node);
      if (/(auto|scroll)/.test(style.overflowY) && node.scrollHeight > node.clientHeight + 1) return;
      if (/(auto|scroll)/.test(style.overflowX) && node.scrollWidth > node.clientWidth + 1) {
        var max = node.scrollWidth - node.clientWidth;
        var step = event.deltaMode === 1 ? event.deltaY * 16 : event.deltaY;
        if ((step < 0 && node.scrollLeft <= 0) || (step > 0 && node.scrollLeft >= max - 1)) return;
        node.scrollLeft += step;
        event.preventDefault();
        return;
      }
    }
  }, { passive: false });

  window.addEventListener("error", function (event) {
    call("log", { level: "error", text: String(event.message || "Ошибка"), line: event.lineno || 0 })
      .catch(function () {});
  });
  window.addEventListener("unhandledrejection", function (event) {
    var reason = event.reason && event.reason.message ? event.reason.message : String(event.reason);
    call("log", { level: "error", text: "Необработанная ошибка: " + reason }).catch(function () {});
  });

  function openStream(action, body, handlers) {
    var options = handlers || {};
    var id = MODULE_ID + ":" + NONCE + ":s" + (++seq);
    var promise = new Promise(function (resolve, reject) {
      streams[id] = {
        resolve: resolve, reject: reject,
        onDelta: options.onDelta, onStatus: options.onStatus,
        onReasoning: options.onReasoning, onUsage: options.onUsage,
        onDone: options.onDone, onError: options.onError
      };
      try {
        parent.postMessage({ type: "memu-module", id: id, from: MODULE_ID,
                           action: action, payload: body || {} }, "*");
      } catch (error) {
        delete streams[id];
        reject(error);
      }
    });
    promise.id = id;
    promise.cancel = function () {
      call("cancel", { id: id }).catch(function () {});
      // Хост может быть уже занят другим: поток закрывается и на месте.
      setTimeout(function () {
        var open = streams[id];
        if (!open) return;
        delete streams[id];
        open.reject(new Error("Остановлено"));
      }, 1500);
    };
    return promise;
  }

  /* ------------------- единая шапка окна (Раунд 30) ------------------- */
  // Модуль не рисует свою верхнюю панель: действия он отдаёт окну MemuAI, и
  // те встают в одну строку с именем модуля и кнопками окна (в развёрнутом
  // виде — прямо в верхнюю панель MemuAI). Хост, который так умеет, грузит
  // документ с env.toolbar = "host"; иначе (стенд, Telegram, старый MemuAI)
  // модуль показывает свою шапку — :root[data-host-toolbar] её прячет.
  var TOOLBAR_LIMIT = 16;
  var toolbarSpec = { title: "", status: "", items: [] };
  var toolbarClicks = Object.create(null);
  var toolbarTimer = null;
  var toolbarWaiters = [];

  function toolbarSupported() { return hostState.toolbar === "host"; }

  function plainToolbarItem(item, parentId) {
    if (!item || typeof item !== "object") return null;
    var out = {};
    Object.keys(item).forEach(function (key) {
      var value = item[key];
      if (typeof value === "function" || key === "menu") return;
      out[key] = value;
    });
    out.id = String(item.id || (item.sep || item.kind === "separator" ? "" : "item")).slice(0, 60);
    var clickKey = parentId ? parentId + "\u0000" + out.id : out.id;
    if (typeof item.onClick === "function") toolbarClicks[clickKey] = item.onClick;
    if (!parentId && Array.isArray(item.menu)) {
      out.menu = item.menu.slice(0, 40).map(function (entry) { return plainToolbarItem(entry, out.id); })
        .filter(Boolean);
    }
    return out;
  }

  function flushToolbar() {
    toolbarTimer = null;
    var waiters = toolbarWaiters;
    toolbarWaiters = [];
    var done = function (result) { waiters.forEach(function (resolve) { resolve(result); }); };
    if (!toolbarSupported()) { done({ ok: false, supported: false }); return; }
    toolbarClicks = Object.create(null);
    var payload = {
      title: String(toolbarSpec.title || "").slice(0, 120),
      status: String(toolbarSpec.status || "").slice(0, 120),
      items: (toolbarSpec.items || []).slice(0, TOOLBAR_LIMIT).map(function (item) { return plainToolbarItem(item, ""); })
        .filter(Boolean)
    };
    call("toolbar", payload).then(function (result) {
      done({ ok: true, supported: true, state: result && result.state || "" });
    }, function (error) {
      done({ ok: false, supported: false, error: String(error && error.message || error) });
    });
  }

  function queueToolbar() {
    return new Promise(function (resolve) {
      toolbarWaiters.push(resolve);
      if (!toolbarTimer) toolbarTimer = setTimeout(flushToolbar, 0);
    });
  }

  function runToolbarClick(data) {
    var id = String(data.id || "");
    var handler = data.value !== undefined && data.value !== null && data.value !== ""
      ? toolbarClicks[id + "\u0000" + String(data.value)] || toolbarClicks[id] : toolbarClicks[id];
    if (handler) {
      try { handler(data); } catch (error) { if (window.console) console.error(error); }
    }
  }

  // Round 38: Memu.appbar — the title bar of the module's own app window (pgstatic/module-appbar.js).
  // set({title, actions, color, text, style, caption}) — title/actions go through Memu.toolbar, colours to the bar;
  // caption: false hides the window's name/icon on the bar (the module shows its own), buttons stay.
  // Elements with data-memu-drag move the window (double click — maximize); state() — {active, height, overlay}.
  var appbarState = { active: false, height: 0, overlay: false, style: "" };
  function applyAppbar(state) {
    appbarState = { active: !!state.active, height: Number(state.height) || 0, overlay: !!state.overlay, style: String(state.style || "") };
    var root = document.documentElement;
    if (!root || !root.style) return;
    root.style.setProperty("--memu-appbar-h", (appbarState.active && appbarState.overlay ? appbarState.height : 0) + "px");
    if (appbarState.active) root.setAttribute("data-memu-appbar", appbarState.overlay ? "overlay" : "bar");
    else root.removeAttribute("data-memu-appbar");
  }
  if (typeof document.addEventListener === "function") document.addEventListener("mousedown", function (event) {
    if (event.button !== 0 || !appbarState.active) return;
    var target = event.target && event.target.closest ? event.target : null;
    var area = target && target.closest("[data-memu-drag]");
    if (!area || area.getAttribute("data-memu-drag") === "false") return;
    // Buttons, fields and sliders inside a drag area keep their own clicks.
    var control = target.closest("button, a[href], input, textarea, select, [role=button], [role=slider], [contenteditable=''], [contenteditable='true'], [data-memu-drag='false']");
    if (control && area.contains(control)) return;
    event.preventDefault();
    call("appbar", { drag: event.detail >= 2 ? "toggle" : "caption" });
  }, true);
  var appbar = {
    set: function (spec) {
      spec = spec || {};
      var jobs = [];
      if (spec.title !== undefined || spec.actions !== undefined) {
        jobs.push(toolbar.set({ title: spec.title || "", status: spec.status || "", items: Array.isArray(spec.actions) ? spec.actions : [] }));
      }
      jobs.push(call("appbar", { color: spec.color || "", text: spec.text || "", background: spec.background || "", dark: typeof spec.dark === "boolean" ? spec.dark : null, style: spec.style || "", caption: spec.caption === false ? "off" : "" }));
      return Promise.all(jobs).then(function (out) { return out[out.length - 1]; });
    },
    state: function () { return { active: appbarState.active, height: appbarState.height, overlay: appbarState.overlay, style: appbarState.style }; },
    on: function (handler) { return Memu.on("appbar", handler); }
  };

  var toolbar = {
    // true — окно MemuAI рисует действия модуля в своей строке заголовка.
    supported: toolbarSupported,
    // spec: массив действий или { title, status, items }. Функции onClick
    // остаются в модуле; окну уходят только данные.
    set: function (spec) {
      if (Array.isArray(spec)) spec = { items: spec };
      spec = spec || {};
      toolbarSpec = { title: spec.title || "", status: spec.status || "", items: Array.isArray(spec.items) ? spec.items.slice() : [] };
      return queueToolbar();
    },
    update: function (id, patch) {
      var found = false;
      toolbarSpec.items = toolbarSpec.items.map(function (item) {
        if (!item || String(item.id) !== String(id)) return item;
        found = true;
        var next = {};
        Object.keys(item).forEach(function (key) { next[key] = item[key]; });
        Object.keys(patch || {}).forEach(function (key) { next[key] = patch[key]; });
        return next;
      });
      return found ? queueToolbar() : Promise.resolve({ ok: false, supported: toolbarSupported(), error: "no item " + id });
    },
    title: function (text) { toolbarSpec.title = String(text || ""); return queueToolbar(); },
    status: function (text) { toolbarSpec.status = String(text || ""); return queueToolbar(); },
    clear: function () { toolbarSpec = { title: "", status: "", items: [] }; return queueToolbar(); },
    items: function () { return toolbarSpec.items.slice(); },
    // Adopt an existing in-page header without rewriting it: the listed buttons move to the window's row (a click
    // there clicks the original), the elements in hide (a logo/title the window already shows) disappear, the text
    // of status becomes the window's status. Labels, disabled and aria-pressed follow the originals live.
    // opts: { buttons: [{ id, el, icon, label?, showLabel?, primary?, pin? }], hide: [selector], status: selector,
    //         title?: string, before?: [items], after?: [items] }
    adopt: function (opts) {
      opts = opts || {};
      if (!toolbarSupported()) return Promise.resolve({ ok: false, supported: false });
      var pick = function (sel) { return typeof sel === "string" ? document.querySelector(sel) : sel; };
      [].concat(opts.hide || []).forEach(function (sel) {
        var nodes = typeof sel === "string" ? document.querySelectorAll(sel) : [sel];
        for (var i = 0; i < nodes.length; i += 1) if (nodes[i]) nodes[i].setAttribute("data-memu-host-hidden", "1");
      });
      var defs = (opts.buttons || []).slice();
      var build = function () {
        var items = defs.map(function (d) {
          var el = pick(d.el);
          if (!el) return null;
          if (!el.hasAttribute("data-memu-host-hidden")) el.setAttribute("data-memu-host-hidden", "1");
          var text = String(el.getAttribute("aria-label") || el.getAttribute("title") || el.textContent || "").replace(/\s+/g, " ").trim();
          var toggle = el.hasAttribute("aria-pressed") || d.kind === "toggle";
          return { id: d.id, icon: d.icon || "", label: typeof d.label === "function" ? d.label() : (d.label || text),
            title: String(el.getAttribute("title") || text), disabled: !!el.disabled || el.getAttribute("aria-disabled") === "true",
            kind: toggle ? "toggle" : "button", pressed: el.getAttribute("aria-pressed") === "true",
            showLabel: !!d.showLabel, primary: !!d.primary, pin: !!d.pin, dot: !!(d.dot && d.dot()),
            onClick: function () { el.click(); } };
        }).filter(Boolean);
        var statusNode = opts.status ? pick(opts.status) : null;
        toolbarSpec = { title: opts.title !== undefined ? String(opts.title || "") : toolbarSpec.title,
          status: statusNode ? String(statusNode.textContent || "").replace(/\s+/g, " ").trim() : toolbarSpec.status,
          items: (opts.before || []).concat(items, opts.after || []) };
        return queueToolbar();
      };
      if (typeof MutationObserver !== "undefined") {
        var timer = null;
        var observer = new MutationObserver(function () { if (!timer) timer = setTimeout(function () { timer = null; build(); }, 30); });
        defs.concat(opts.status ? [{ el: opts.status }] : []).forEach(function (d) {
          var el = pick(d.el);
          if (el) observer.observe(el, { attributes: true, childList: true, subtree: true, characterData: true });
        });
      }
      return build();
    },
    // on(handler) — любое действие; on(id, handler) — одно.
    on: function (id, handler) {
      if (typeof id === "function") return Memu.on("toolbar", id);
      return Memu.on("toolbar", function (data) { if (data && String(data.id) === String(id)) handler(data); });
    }
  };

  // Memu.music(body) — music generation (permission media); Memu.music.call(op, args) — the MemuMusic
  // library / downloader / player (round 37, permission music, operations in app/music/api.py).
  function musicApi(body) { return call("music", body || {}); }
  musicApi.call = function (op, args) { return call("music_call", { op: String(op || ""), args: args || {} }); };
  musicApi.streamUrl = function (id) { return musicApi.call("stream_url", { id: id }); };
  musicApi.coverUrl = function (id) { return musicApi.call("cover_url", { id: id }); };
  // Round 40 (r40-learn): Memu.learn.call(op, args) — MemuLearn courses / cards / FSRS (permission learn, app/learn/api.py).
  var learnApi = { call: function (op, args) { return call("learn_call", { op: String(op || ""), args: args || {} }); } };

  var Memu = {
    id: MODULE_ID,
    version: 4,
    toolbar: toolbar,
    appbar: appbar,
    // Round 38: settings of this module's own widgets / panels in MemuAI (contributes; module-integration.js).
    integration: {
      list: function () { return call("integration", { op: "list" }); },
      set: function (kind, id, values) { return call("integration", { op: "set", kind: kind, id: id, values: values || {} }); }
    },
    manifest: MANIFEST,
    saved: __MEMU_SAVED__,
    env: hostState,
    call: call,
    request: function (body) { return call("request", body); },
    stream: function (body, handlers) { return openStream("stream", body, handlers); },
    // Round 29: a full message array (system/user/assistant) and sampler
    // parameters; handlers.onReasoning gets the model's thoughts, onUsage the
    // provider's token usage.
    complete: function (body, handlers) { return openStream("complete", body, handlers); },
    store: {
      get: function (key, fallback) {
        return call("store", { op: "get", key: key }).then(function (r) {
          return r && r.exists ? r.value : (fallback === undefined ? null : fallback);
        });
      },
      getMany: function (keys) { return call("store", { op: "get_many", keys: keys || [] }).then(function (r) { return (r && r.values) || {}; }); },
      set: function (key, value) { return call("store", { op: "set", key: key, value: value }); },
      remove: function (key) { return call("store", { op: "remove", key: key }); },
      list: function (prefix) { return call("store", { op: "list", prefix: prefix || "" }).then(function (r) { return (r && r.keys) || []; }); },
      usage: function () { return call("store", { op: "usage" }); }
    },
    asset: function (name) {
      return call("asset", { name: name }).then(function (r) { return r && r.json !== undefined ? r.json : (r ? r.text : null); });
    },
    // Round 29: download a character card or a lorebook by link or id (chub, JanitorAI, Pygmalion,
    // AICharacterCards, RisuRealm, Perchance, a direct PNG/JSON link on an allowed host). Needs the
    // «net-import» permission; MemuAI asks the person before the first download from each site.
    // opts.kind: "auto" | "character" | "lorebook"; opts.resolve: true only recognises the input (no network).
    fetchImport: function (input, opts) {
      return call("net_import", { input: String(input || ""), kind: (opts && opts.kind) || "auto", op: opts && opts.resolve ? "resolve" : "fetch" });
    },
    save: function (fields) { return call("save", fields); },
    models: function () { return call("models", {}); },
    image: function (body) { return call("image", body); },
    video: function (body) { return call("video", body); },
    speak: function (body) { return call("speak", body); },
    transcribe: function (body) { return call("transcribe", body); },
    // Микрофон доступен только окну MemuAI: страница модуля живёт в песочнице
    // без собственного источника, и браузер отказывает ей в getUserMedia.
    record: {
      start: function () { return call("record", { action: "start" }); },
      stop: function () { return call("record", { action: "stop" }); },
      cancel: function () { return call("record", { action: "cancel" }); }
    },
    extract: function (body) { return call("extract", body); },
    handoff: function (body) { return call("handoff", body); },
    download: function (body) { return call("download", body); },
    notify: function (body) { return call("notify", body); },
    monitor: function (body) { return call("monitor", body || {}); },
    music: musicApi,
    learn: learnApi,
    open: function (body) { return call("open", body); },
    search: function (body) { return call("search", body); },
    fetch: function (body) { return call("fetch", body); },
    copy: function (text) { return call("clipboard", { text: text }); },
    paste: function () { return call("clipboard_read", {}).then(function (r) { return (r && r.text) || ""; }); },
    routes: function () { return call("routes", {}); },
    env: function () { return call("env", {}); },
    log: function (text, level) { return call("log", { text: String(text), level: level || "info" }); },
    resize: function (body) { return call("resize", body); },
    history: function () { return call("chat_history", {}); },
    close: function () { return call("close", {}); },
    // Round 31: unsaved(true, "text") — closing the window from MemuAI asks the
    // person first; unsaved(false) after saving. Memu.close() never asks.
    unsaved: function (flag, text) {
      return call("unsaved", { unsaved: !!flag, text: String(text || "").slice(0, 200) });
    },
    on: function (type, handler) {
      (events[type] = events[type] || []).push(handler);
      return function () { events[type] = (events[type] || []).filter(function (item) { return item !== handler; }); };
    },
    onTheme: function (handler) { return this.on("theme", handler); },
    onLanguage: function (handler) { return this.on("language", handler); },
    t: translate,
    i18n: i18n,
    ready: function () { document.documentElement.setAttribute("data-module-ready", "1"); return true; }
  };
  Object.defineProperty(Memu, "lang", { enumerable: true, get: function () { return hostState.lang || "ru"; } });
  window.Memu = Memu;
  window.MemuAI = Memu;
  // The markup is already parsed: translate it before the module's own code
  // runs, so nothing flashes in the wrong language.
  applyTranslations(document);
  if (toolbarSupported()) document.documentElement.setAttribute("data-host-toolbar", "1");
  if (typeof window.__memuModuleMain === "function") window.__memuModuleMain();
})();
""".strip()


def module_document(package: Dict[str, Any], saved: Optional[Dict[str, Any]] = None,
                    *, env: Optional[Dict[str, Any]] = None) -> str:
    """The page that runs inside the module window."""
    public = {
        "id": package.get("id"),
        "title": package.get("title"),
        "title_en": package.get("title_en"),
        "version": package.get("version"),
        "icon": package.get("icon"),
        "window": package.get("window") or {},
        "persist": package.get("persist") or [],
        "permissions": package.get("permissions") or [],
        "bridge": package.get("bridge") or {},
        "builtin": bool(package.get("builtin")),
        "default_locale": str(package.get("default_locale") or "ru"),
    }
    environment = {
        "module": str(package.get("id") or ""),
        "lang": "ru",
        "theme": "dark",
        "accent_name": "terracotta",
        "accent": "",
        "surface": "",
        "density": "normal",
        "font_scale": 100,
        "platform": "win32",
        "host": "MemuAI",
    }
    environment.update({key: value for key, value in (env or {}).items() if value is not None})
    # Значения ниже уходят прямо в HTML-атрибуты. Приходят они и из тела
    # запроса на предпросмотр, поэтому каждое приводится к известному виду:
    # без этого кавычка в lang закрывала атрибут и добавляла свой тег.
    environment = _normalize_env(environment)
    bridge = (BRIDGE_JS
              .replace("__MEMU_MODULE_ID__", _script_json(str(package.get("id") or "")))
              .replace("__MEMU_ENV__", _script_json(environment))
              .replace("__MEMU_MANIFEST__", _script_json(public))
              .replace("__MEMU_SAVED__", _script_json(saved or {}))
              .replace("__MEMU_I18N__", _script_json(normalize_i18n(package.get("i18n") or {}))))
    accent = str(package.get("accent") or "")
    accent_rule = f"\nhtml{{--accent:{accent};--accent-soft:color-mix(in srgb,{accent} 16%,transparent)}}\n" \
        if re.fullmatch(r"#[0-9a-fA-F]{3,8}", accent) else ""
    color_scheme = "dark" if environment["theme"] == "dark" else "light"
    # Round 37: the «music» permission lets <audio>/<img> load MemuMusic stream/cover URLs (token-checked).
    from .music.api import csp_sources as music_csp
    music_src = " " + music_csp() if "music" in (package.get("permissions") or []) else ""
    return (
        "<!DOCTYPE html>\n"
        f'<html lang="{_attr(environment["lang"])}" data-theme="{_attr(environment["theme"])}" '
        f'data-accent="{_attr(environment["accent_name"])}" '
        f'data-surface="{_attr(environment.get("surface") or "")}" '
        f'data-density="{_attr(environment.get("density") or "normal")}"'
        + (' data-host-toolbar="1"' if environment.get("toolbar") == "host" else "") + ">\n"
        "<head>\n<meta charset=\"utf-8\">\n"
        # Политика и в самом документе: srcdoc-предпросмотр мастерской не
        # получает заголовков ответа, и без неё код модуля ходил в сеть.
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; "
        f"script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:{music_src}; "
        f"media-src data: blob:{music_src}; font-src data:; connect-src 'none'; object-src 'none'; "
        "base-uri 'none'; form-action 'none'\">\n"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
        f'<meta name="color-scheme" content="{color_scheme}">\n'
        "<title>" + str(package.get("title") or "Модуль").replace("<", "&lt;") + "</title>\n"
        "<style>" + token_css(environment) + "</style>\n"
        "<style>" + BASE_CSS + accent_rule + "</style>\n"
        "<style>" + _embed_style(package.get("css") or "") + "</style>\n"
        "</head>\n<body>\n"
        + (_embed_html(package.get("html") or "") if (package.get("html") or "").strip()
           or (package.get("js") or "").strip() else
           '<div class="mu-page"><div class="mu-empty"><b>У модуля нет кода</b>'
           '<span>Откройте «Настройки → Модули → Код» и добавьте HTML или JavaScript.</span></div></div>')
        + "\n<script>" + _memu_keys_js() + "</script>"   # r33: shortcuts in any keyboard layout
        + "\n<script>" + bridge + "</script>\n"
        # Переводы строк вокруг кода обязательны: модуль, чей код кончается
        # строчным комментарием «// ...», иначе закомментировал бы «};» и не
        # запускался вовсе.
        + '<script>window.__memuModuleMain=function(){\n' + _embed_script(package.get("js") or "") + '\n};</script>\n'
        + '<script>window.__memuModuleMain();</script>\n'
        "</body>\n</html>\n"
    )


# --------------------------------------------------------------------------
# Проверка пакета
# --------------------------------------------------------------------------

def _object_entries(body: str) -> List[str]:
    """Top-level entries of an object literal body, split on commas.

    Commas inside calls, arrays and strings (``a: f(x, y)``) do not split.
    """
    entries: List[str] = []
    depth = 0
    quote = ""
    current = ""
    for char in body:
        if quote:
            current += char
            if char == quote:
                quote = ""
            continue
        if char in "\"'`":
            quote = char
        elif char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            entries.append(current.strip())
            current = ""
            continue
        current += char
    if current.strip():
        entries.append(current.strip())
    return [entry for entry in entries if entry and not entry.startswith("...")]


def validate_module_source(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Cheap checks the workbench shows before saving."""
    problems: List[Dict[str, str]] = []
    html = str(spec.get("html") or "")
    css = str(spec.get("css") or "")
    js = str(spec.get("js") or "")
    if re.search(r"</\s*(html|body)\s*>", html, re.I):
        problems.append({"level": "warn", "field": "html",
                         "ru": "Разметка модуля попадает внутрь страницы MemuAI: закрывающие </body> и </html> не нужны.",
                         "en": "The markup is inserted inside the page: </body> and </html> are not needed."})
    if re.search(r"<(script|link)\b", html, re.I):
        problems.append({"level": "warn", "field": "html",
                         "ru": "Внешние script и link не загружаются: у модуля нет доступа к сети.",
                         "en": "External script and link tags do not load: a module has no network access."})
    if re.search(r"</\s*script\s*>", js, re.I):
        problems.append({"level": "warn", "field": "js",
                         "ru": "Закрывающий </script> внутри кода обрывает модуль. Уберите его или экранируйте как <\\/script>.",
                         "en": "A closing </script> inside the code truncates the module. Remove it or escape it as <\\/script>."})
    if re.search(r"</\s*style\s*>", css, re.I):
        problems.append({"level": "warn", "field": "css",
                         "ru": "Закрывающий </style> внутри стилей обрывает модуль.",
                         "en": "A closing </style> inside the styles truncates the module."})
    for pattern, label in ((r"\bhttp://", "http://"), (r"\bhttps://", "https://")):
        if re.search(pattern, js):
            problems.append({"level": "info", "field": "js",
                             "ru": f"Адрес {label} в коде не откроется напрямую: используйте Memu.fetch, Memu.download или Memu.open.",
                             "en": f"A {label} address cannot be fetched directly: use Memu.fetch, Memu.download or Memu.open."})
            break
    persist = spec.get("persist") if isinstance(spec.get("persist"), list) else []
    used: set = set()
    # Ключи объектов-литералов в Memu.save: «ключ: значение», "ключ": значение
    # и сокращённая запись { a, b }. Раньше сокращённую запись — ту самую, что
    # в шаблоне «Инструмент», — проверка не видела вовсе.
    for chunk in re.findall(r"Memu\.save\(\s*\{([^{}]*)\}", js):
        for entry in _object_entries(chunk):
            match = (re.match(r"^([A-Za-z_$][\w$]*)\s*:", entry)
                     or re.match(r"^[\"']([^\"']+)[\"']\s*:", entry)
                     or re.match(r"^([A-Za-z_$][\w$]*)$", entry))
            if match:
                used.add(match.group(1))
    missing = sorted(name for name in used if name not in persist)
    if missing:
        problems.append({"level": "warn", "field": "persist",
                         "ru": "Эти поля не сохранятся: " + ", ".join(missing) +
                               ". Добавьте их в список сохраняемых полей.",
                         "en": "These fields will not persist: " + ", ".join(missing) +
                               ". Add them to the saved keys list."})
    if not js.strip():
        problems.append({"level": "info", "field": "js",
                         "ru": "Код пустой: окно покажет только разметку и стили.",
                         "en": "The script is empty: the window shows only markup and styles."})
    try:
        translations = normalize_i18n(spec.get("i18n") or {})
    except ValueError as exc:
        translations = {}
        problems.append({"level": "warn", "field": "i18n", "ru": str(exc),
                         "en": "Translations: " + str(exc).split(": ", 1)[-1]})
    used = i18n_keys_used(html, js)
    if translations:
        for locale, table in sorted(translations.items()):
            missing_keys = [key for key in used if key not in table]
            if missing_keys:
                problems.append({"level": "warn", "field": "i18n",
                                 "ru": f"В переводе «{locale}» нет ключей: " + ", ".join(missing_keys[:12])
                                       + (" и ещё " + str(len(missing_keys) - 12) if len(missing_keys) > 12 else ""),
                                 "en": f"Locale “{locale}” lacks keys: " + ", ".join(missing_keys[:12])
                                       + (f" and {len(missing_keys) - 12} more" if len(missing_keys) > 12 else "")})
        if len(translations) == 1:
            only = next(iter(translations))
            problems.append({"level": "info", "field": "i18n",
                             "ru": f"Модуль переведён только на «{only}». Добавьте второй язык, чтобы модуль переключался вместе с MemuAI.",
                             "en": f"The module has only the “{only}” locale. Add a second one so it follows the MemuAI language."})
    elif used:
        problems.append({"level": "warn", "field": "i18n",
                         "ru": "Код использует ключи перевода, но переводов нет: на экране будут видны сами ключи.",
                         "en": "The code uses translation keys but there are no translations: the keys themselves will show."})
    model_calls = ("Memu.request", "Memu.stream", "Memu.complete", "Memu.image", "Memu.video",
                   "Memu.speak", "Memu.transcribe", "Memu.music")
    if "Memu." in js and not any(call in js for call in model_calls):
        problems.append({"level": "info", "field": "js",
                         "ru": "Модуль не обращается к моделям. Мост Memu доступен в любой момент.",
                         "en": "The module never talks to a model. The Memu bridge is always available."})
    return {"problems": problems, "ok": not any(item["level"] == "warn" for item in problems)}
