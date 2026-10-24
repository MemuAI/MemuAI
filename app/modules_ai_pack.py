# -*- coding: utf-8 -*-
"""The modules «AI pack»: one .txt with everything an AI agent needs to write a MemuAI module (Round 29).

Settings → Documentation → Modules → «AI pack» downloads it (RU or EN). The person attaches the file to
any AI agent (or to MemuAI itself) and says «make me module X, the instructions are in the file».

The pack never goes stale because most of it is assembled from the source of truth at download time:

* the instructions for the AI and the checklist (written here, per language);
* a generated reference read from the code: ``modules_host`` (manifest defaults and limits, permissions,
  launcher slots, the bridge source ``BRIDGE_JS`` — methods, timeouts, i18n attributes, events —, the
  module document's Content-Security-Policy, design tokens and ``mu-*`` classes of ``BASE_CSS``,
  the wizard templates) and ``modules_validate`` (the sandbox rules = «common mistakes»);
* every article of the «Modules» group of the documentation (``app/docs/<lang>/modules*.md``, in the
  order of ``index.json``) — new pages, e.g. from other workstreams, are picked up automatically;
* complete working example modules (``app/docs/module-examples/*``, validated by the tests);
* optionally the Node.js test harness ``memu-module-test.js`` with the real bridge inside.

Routes (``register(app)``):

* ``GET  /api/pg/modules-ai/pack?lang=ru|en&harness=1&format=txt|json`` — the pack;
* ``GET  /api/pg/modules-ai/harness?lang=…`` — the harness alone (``memu-module-test.js``);
* ``POST /api/pg/modules-ai/validate`` — validate a package before import: ``{text}`` (an AI answer with
  the files in code blocks, or a JSON package), ``{zip: base64}``, ``{files: {name: text}}`` or
  ``{package: {...}}``; answers the report plus the normalized package for ``/api/pg/modules/import``.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Request  # module level: route annotations are strings (from __future__ import annotations)

from . import docs as docs_module
from . import modules_host as host
from . import modules_validate as validate
from . import token_estimate

PACK_FORMAT = 1


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

def _app_dir() -> Path:
    base = getattr(sys, "_MEIPASS", "")
    if base and (Path(base) / "app").is_dir():
        return Path(base) / "app"
    return Path(__file__).resolve().parent


def examples_dir() -> Path:
    return docs_module.docs_dir() / "module-examples"


def harness_template_path() -> Path:
    return _app_dir() / "pgstatic" / "memu-module-test.js"


def _version() -> str:
    try:
        from .version import APP_VERSION
        return str(APP_VERSION)
    except Exception:  # pragma: no cover - version.py always ships
        return ""


# --------------------------------------------------------------------------
# Facts read from the code
# --------------------------------------------------------------------------

def bridge_timeouts() -> Dict[str, int]:
    match = re.search(r"var TIMEOUTS = \{(.*?)\};", host.BRIDGE_JS, re.S)
    rows: Dict[str, int] = {}
    for name, value in re.findall(r"(\w+):\s*(\d+)", match.group(1) if match else ""):
        rows[name] = int(value)
    return rows


def i18n_attributes() -> List[str]:
    match = re.search(r"var I18N_ATTRS = \[(.*?)\]", host.BRIDGE_JS, re.S)
    return re.findall(r"\"([\w-]+)\"", match.group(1) if match else "")


def csp() -> str:
    document = host.module_document({"id": "x", "html": "<div></div>", "js": ""}, {})
    match = re.search(r'Content-Security-Policy" content="([^"]+)"', document)
    return match.group(1) if match else ""


def mu_classes() -> List[str]:
    return sorted(set(re.findall(r"\.(mu-[a-z0-9-]+)", host.BASE_CSS)))


def bridge_reference() -> List[Tuple[str, str, str, Optional[int]]]:
    """(method, bridge action, permission, timeout ms) for every Memu.* function."""
    timeouts = bridge_timeouts()
    rows = []
    for method, action in validate.bridge_methods().items():
        if method in ("id", "version", "manifest", "saved", "env", "lang", "i18n", "record", "toolbar"):
            continue
        permission = host.ACTION_PERMISSION.get(action, "") if action else ""
        # an action missing from TIMEOUTS waits the bridge default of 120 s (callWithId)
        rows.append((method, action, permission, timeouts.get(action, 120000) if action else None))
    return rows


def rules() -> Dict[str, Any]:
    """What the test harness needs from this build (also serialised into it)."""
    def table(items):
        return [{"pattern": pattern, "level": level, "ru": ru, "en": en} for pattern, level, ru, en in items]
    return {
        "app_version": _version(),
        "permissions": list(host.PERMISSIONS),
        "action_permission": dict(host.ACTION_PERMISSION),
        "method_permissions": validate.method_permissions(),
        "free_actions": list(host.FREE_ACTIONS),
        "builtin_ids": list(host.BUILTIN_ORDER),
        "id_pattern": host._ID_RE.pattern,
        "launcher_slots": list(host.LAUNCHER_SLOTS),
        "code_limit": host._CODE_LIMIT,
        "persist_limit": host._PERSIST_LIMIT,
        "routes": host.media_routes(),
        "js_rules": table(validate._JS_SANDBOX_RULES),
        "js_raw_rules": table(validate._JS_RAW_RULES),
        "html_rules": table(validate._HTML_RULES),
        "css_rules": table(validate._CSS_RULES),
        "icons": list(validate.ICON_NAMES),
    }


def harness_source() -> str:
    """``memu-module-test.js`` with the real bridge and this build's rules inside."""
    template = harness_template_path().read_text(encoding="utf-8")
    bridge = json.dumps(host.BRIDGE_JS, ensure_ascii=False)
    data = json.dumps(rules(), ensure_ascii=False)
    out = template.replace("/*@bridge*/null/*@end*/", bridge, 1).replace("/*@rules*/null/*@end*/", data, 1)
    # "</script>" never matters in Node, but a stray "*/" inside JSON strings would end a comment
    # only if it were inside one — the values sit in expressions, so the file stays valid JS.
    return out


# --------------------------------------------------------------------------
# Documentation → plain text
# --------------------------------------------------------------------------

def _doc_titles(lang: str) -> Dict[str, str]:
    catalog = docs_module.catalog(lang)
    return {page["id"]: page["title"] for group in catalog["groups"] for page in group["pages"]}


def _plain_markdown(body: str, titles: Dict[str, str], lang: str) -> str:
    see = "see" if lang == "en" else "см."
    out: List[str] = []
    fence = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
            out.append(line)
            continue
        if fence:
            out.append(line)
            continue

        def link(match: "re.Match[str]") -> str:
            label, target = match.group(1), match.group(2)
            if target.startswith("doc:"):
                page = target[4:].split("#", 1)[0]
                return f"{label} ({see} «{titles.get(page, page)}»)" if lang != "en" else f"{label} ({see} “{titles.get(page, page)}”)"
            if target.startswith("#"):
                return label
            return f"{label} ({target})"
        line = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, line)
        line = re.sub(r"\+\+([^\s+](?:[^\n]*?[^\s+])??)\+\+", r"\1", line)
        callout = re.match(r"^(\s*>\s*)\[!(NOTE|TIP|WARNING|IMPORTANT|DANGER)\]\s*(.*)$", line, re.I)
        if callout:
            line = f"{callout.group(1)}{callout.group(2).upper()}: {callout.group(3)}".rstrip()
        out.append(line)
    return "\n".join(out).strip() + "\n"


def module_doc_pages(lang: str) -> List[Dict[str, Any]]:
    """Every page of the «modules» group, in index order (new pages are picked up automatically)."""
    catalog = docs_module.catalog(lang)
    pages: List[Dict[str, Any]] = []
    for group in catalog["groups"]:
        if group["id"] != "modules":
            continue
        for row in group["pages"]:
            if row["id"] == "modules-ai-pack":
                continue                       # the page about this very file
            page = docs_module.page(lang, row["id"])
            if page:
                pages.append(page)
    return pages


# --------------------------------------------------------------------------
# Examples
# --------------------------------------------------------------------------

EXAMPLE_ORDER = ("quick-notes", "model-chat", "doc-brief")


def examples() -> List[Dict[str, Any]]:
    """Complete packages from app/docs/module-examples (folders), each as {id, files…}."""
    rows = []
    folder = examples_dir()
    names = [name for name in EXAMPLE_ORDER if (folder / name).is_dir()]
    names += sorted(p.name for p in folder.iterdir() if p.is_dir() and p.name not in names) if folder.is_dir() else []
    for name in names:
        try:
            source = validate.load_source(folder / name)
        except ValueError:
            continue
        rows.append({"id": name, "manifest": source["manifest"], "files": source["files"]})
    return rows


def template_packages() -> List[Dict[str, Any]]:
    rows = []
    for row in host.TEMPLATES:
        spec = host.template_manifest(row["id"], "my-" + row["id"], "")
        manifest = {key: spec[key] for key in ("id", "title", "title_en", "summary", "summary_en", "icon", "window",
                                                "persist", "permissions")}
        rows.append({"id": row["id"], "title_ru": row["title_ru"], "title_en": row["title_en"], "manifest": manifest,
                     "files": {"index.html": spec["html"], "style.css": spec["css"], "main.js": spec["js"],
                               "i18n.json": json.dumps(spec["i18n"], ensure_ascii=False, indent=2)}})
    return rows


# --------------------------------------------------------------------------
# The text
# --------------------------------------------------------------------------

TEXT: Dict[str, Dict[str, Any]] = {
    "ru": {
        "title": "MemuAI — пакет для ИИ: как написать модуль",
        "usage": [
            "КАК ПОЛЬЗОВАТЬСЯ (для человека)",
            "1. Приложите этот файл к любому ИИ-агенту (ChatGPT, Claude, Gemini, Cursor, MemuCode или чат MemuAI).",
            "2. Напишите: «Сделай мне модуль MemuAI: <что он должен делать>. Все инструкции — в приложенном файле».",
            "3. Ответ ИИ (файлы модуля) вставьте целиком в «Настройки → Модули → Импорт → Проверить и импортировать»",
            "   или сохраните файлы в папку / .zip и выберите их там же. MemuAI проверит пакет и покажет отчёт.",
            "4. Включите модуль переключателем — кнопка появится в боковой панели.",
        ],
        "ai_head": "ЧАСТЬ 0. ЗАДАНИЕ ДЛЯ ИИ — прочитай целиком, прежде чем писать код",
        "ai": """Ты пишешь МОДУЛЬ для MemuAI — настольного ИИ-приложения для Windows. Модуль — маленькое
приложение на HTML + CSS + JavaScript, которое MemuAI открывает в своём окне (iframe-песочница).
Всё, что модулю нужно от MemuAI (модели, генерация картинок, речь, файлы, сохранение), идёт через
один объект-мост `Memu`. Ниже — полный и точный справочник, собранный из исходного кода MemuAI.

ЧТО СДАТЬ. Ровно пять файлов, каждый в отдельном блоке кода, перед блоком — строка с именем файла:

    ### FILE: manifest.json
    ```json
    { ... }
    ```
    ### FILE: index.html
    ```html
    ...
    ```
    ### FILE: style.css
    ### FILE: main.js
    ### FILE: i18n.json

MemuAI сам разбирает такой ответ (кнопка «Проверить и импортировать»). Не добавляй других файлов:
модуль состоит только из этих пяти. Не сокращай код многоточиями — файлы должны быть полными.

ЖЁСТКИЕ ПРАВИЛА (нарушение = модуль не работает):
1. Сети нет. fetch, XMLHttpRequest, WebSocket, внешние <script>, <link>, шрифты, картинки и CDN не
   загрузятся (CSP: connect-src 'none', img-src data: blob:). Никаких библиотек: только чистый JS.
   Данные из интернета — Memu.fetch / Memu.search; ответы моделей — Memu.request / Memu.stream.
2. index.html — ФРАГМЕНТ внутри <body>: без <!DOCTYPE>, <html>, <head>, <body>, <script>, <link>.
3. main.js выполняется внутри обычной функции после разбора разметки: НЕ ES-модуль (никаких import/
   export), нет await на верхнем уровне (оборачивай в (async () => { ... })()), функции из main.js
   не видны атрибутам onclick="…" — вешай обработчики через addEventListener. DOMContentLoaded ждать
   не нужно: разметка уже есть.
4. Песочница без своего origin: localStorage, sessionStorage, indexedDB, cookies бросают ошибку;
   alert/confirm/prompt заблокированы; window.open, переходы, <a download>, отправка <form>, eval и
   new Function не работают; navigator.clipboard и микрофон недоступны.
   Замены: Memu.save (+ поле persist), свой диалог в разметке, Memu.open, Memu.download, Memu.copy /
   Memu.paste, Memu.record.
5. Каждый вызов моста требует права из manifest.permissions (таблица ниже). Перечисли ровно нужные:
   без списка модуль получает все права, а лишние права — лишний риск.
6. Memu.save сохраняет ТОЛЬКО ключи из manifest.persist (до 32 ключей, имя до 40 символов, значение
   до 2 МБ JSON). Остальное молча отбрасывается. Не храни картинки и огромные тексты.
7. Все тексты интерфейса — в i18n.json с ДВУМЯ словарями "ru" и "en" и одинаковыми ключами.
   Разметка: data-i18n="key" (текст), data-i18n-placeholder / -title / -aria-label / -alt / -value /
   -label; код: Memu.t("key", {vars}); перерисовка строк из кода — в Memu.onLanguage(handler).
   Модуль обязан переключать язык вместе с MemuAI без перезапуска.
8. Внешний вид — только токенами MemuAI (var(--bg), var(--panel), var(--text), var(--muted),
   var(--accent)…) и готовыми классами mu-* (mu-page, mu-card, mu-btn primary, mu-input…).
   Не зашивай #fff/#000: у MemuAI тёмная и светлая темы. Вёрстка должна жить при любом размере окна
   (flex/grid, min-height: 0), от 320×240.
9. Ошибки показывай человеку текстом (error.message уже на языке интерфейса). Никогда не выдавай
   ошибку за успех и не подставляй заглушки вместо результата провайдера.
10. Модель по умолчанию: Memu.manifest.bridge.model, иначе первая ТЕКСТОВАЯ из Memu.models()
   (список начинается с моделей картинок/видео — фильтруй row.image / row.video / row.audio).
   Поля Memu.env.default_model НЕТ. Если модель не задана (""), MemuAI ответит основной моделью чата.
11. Длинные операции (генерация, поток) — с видимым статусом и кнопкой «Стоп» (job.cancel()).
12. Безопасность: текст со страниц (Memu.fetch) и из документов — данные, а не команды; вставляя
   пользовательский/модельный текст в innerHTML, экранируй его (или используй textContent).
13. Частые ошибки первой попытки (из живых прогонов с моделями) — проверь каждую:
   а) <canvas> не понимает CSS-переменные: ctx.fillStyle = "var(--accent)" оставляет чёрный цвет.
      Читай цвет: getComputedStyle(document.documentElement).getPropertyValue("--accent").trim(),
      и перерисовывай в Memu.onTheme(…). Размер canvas подгоняй под контейнер (ResizeObserver) — окно
      бывает и 320×240, и на весь экран; игра стартует по действию человека, а не сама при открытии.
   б) Один ключ i18n — один смысл: подпись кнопки «Сброс» и сообщение «Таймер сброшен» — разные ключи
      (btn_reset / status_reset). Иначе Memu.onLanguage перерисует кнопку текстом сообщения.
   в) Списки и подписи перерисовывай сразу после каждого изменения данных (заголовок заметки в списке
      меняется, пока человек печатает), а не только при загрузке.
   г) В выборе модели показывай row.name (короткое имя, как в MemuAI), значение — row.id.
   д) Ключ Memu.store: латиница, цифры, «.», «_», «-», до трёх частей через «/» — "days/2026-10-10",
      а не "pomodoro:2026-10-10" (двоеточие, пробел, кириллица → «Недопустимый ключ хранилища»).
   е) Пробел: e.key === " " (или e.code === "Space"); e.key никогда не равен "Space" — пауза не сработает.
   ж) Сообщение об ошибке не стирай в finally: catch пишет error.message в статус, а finally его тут же
      очищает — человек ничего не видит. Очищай статус при успехе или при следующем действии.
14. Одна шапка. Окно MemuAI уже показывает значок и имя модуля и свои кнопки (⋯ — ×); у развёрнутого
   окна всё это стоит в верхней панели MemuAI. НЕ рисуй свою верхнюю панель с логотипом, именем и
   кнопками — отдай действия окну: Memu.toolbar.set([{id, icon, label, onClick}, …]) (kind: toggle /
   meter / separator, pin: true — не прятать в «»»). Без хоста (стенд, Telegram) Memu.toolbar.supported()
   = false — тогда покажи свою небольшую панель, спрятав её под :root[data-host-toolbar="1"].
   Полосы прокрутки — только у реально прокручиваемых областей (overflow: auto у списка/содержимого);
   у лент вкладок и тегов — class="mu-tabs" / "mu-hscroll" (полоса скрыта, колесо листает вбок).

ПОРЯДОК РАБОТЫ:
1) Пойми задачу; если чего-то не хватает — выбери разумный вариант и коротко скажи об этом.
2) Выбери id (латиница в нижнем регистре, цифры, дефис; с буквы; ≤41), права, persist, размер окна.
3) Напиши пять файлов. Начни с разметки на mu-* классах, затем main.js, затем i18n.json.
4) Пройди ЧЕК-ЛИСТ в конце этого файла и исправь всё, что не сходится.
5) Если можешь запускать код — сохрани файлы в папку и прогони тестовый стенд из приложения A:
   `node memu-module-test.js ./папка --click-all` (и `--lang en`). Исправь ошибки до сдачи.
6) В конце ответа — 2–4 строки: что делает модуль и какие права/настройки ему нужны.""",
        "facts_head": "ЧАСТЬ 1. КОРОТКО О ГЛАВНОМ",
        "ref_head": "ЧАСТЬ 2. СПРАВОЧНИК, СОБРАННЫЙ ИЗ КОДА MemuAI {version}",
        "docs_head": "ЧАСТЬ 3. ДОКУМЕНТАЦИЯ ПО МОДУЛЯМ (все статьи раздела «Модули»)",
        "examples_head": "ЧАСТЬ 4. ПОЛНЫЕ РАБОЧИЕ ПРИМЕРЫ (проверены тестами MemuAI)",
        "templates_head": "ЧАСТЬ 4Б. ШАБЛОНЫ МАСТЕРА СОЗДАНИЯ (то, что MemuAI кладёт в новый модуль)",
        "mistakes_head": "ЧАСТЬ 5. ЧАСТЫЕ ОШИБКИ (правила проверки MemuAI)",
        "checklist_head": "ЧАСТЬ 6. ЧЕК-ЛИСТ ПЕРЕД СДАЧЕЙ",
        "testing_head": "ЧАСТЬ 7. КАК ПРОВЕРИТЬ МОДУЛЬ",
        "harness_head": "ПРИЛОЖЕНИЕ A. memu-module-test.js — тестовый стенд (Node.js 18+, без зависимостей)",
        "extras_head": "ДОПОЛНЕНИЯ ДРУГИХ РАЗДЕЛОВ MemuAI",
        "checklist": [
            "Пять файлов, полные, с правильными именами; manifest.json и i18n.json — валидный JSON (без комментариев и висячих запятых).",
            "id: ^[a-z][a-z0-9-]{0,40}$, не translator / studio / design / benchmarks / catalog / api-models.",
            "title и title_en, summary и summary_en заполнены; icon — из списка иконок.",
            "permissions перечисляет КАЖДОЕ право, нужное вызванным методам Memu.*, и ничего лишнего.",
            "Каждый ключ в Memu.save({...}) есть в persist; данные читаются из Memu.saved при старте.",
            "Нет fetch/XMLHttpRequest/WebSocket, внешних URL в src/href/url(), <script>/<link> в разметке.",
            "Нет localStorage/sessionStorage/indexedDB/cookie, alert/confirm/prompt, window.open, eval, import/export, await вне функции.",
            "Все элементы, которые ищет main.js (#id), есть в index.html; обработчики — через addEventListener.",
            "Все видимые тексты — через data-i18n* или Memu.t; в ru и en одинаковые ключи; строки из кода перерисовываются в Memu.onLanguage.",
            "Цвета — только var(--…); проверено мысленно в тёмной и светлой теме; вёрстка не ломается в узком окне.",
            "Модель выбирается из Memu.models() (только текстовые для чата) или из manifest.bridge.model; без модели — понятная ошибка.",
            "Каждый await вызова моста — в try/catch с показом error.message; у долгих операций есть статус и «Стоп».",
            "Текст пользователя и модели вставляется через textContent или экранируется перед innerHTML.",
            "Esc внутри модуля, если он что-то закрывает, вызывает event.preventDefault().",
            "Своей шапки с логотипом/именем/кнопками нет: действия — в Memu.toolbar.set(...); запасная панель скрыта под :root[data-host-toolbar=\"1\"].",
            "Полоса прокрутки есть только там, где содержимое не помещается; у лент вкладок/тегов её не видно (mu-tabs / mu-hscroll).",
            "Вызывается Memu.ready() после первой отрисовки (необязательно, но помогает хосту).",
        ],
        "testing": """В MemuAI: «Настройки → Модули → Импорт → Проверить и импортировать» принимает ответ ИИ целиком
(блоки ### FILE: …), .zip папки модуля, сами пять файлов или экспортный .memu-module.json, и показывает
отчёт: ошибки (импорт не имеет смысла), предупреждения, подсказки. Там же — кнопка импорта.
После импорта модуль открывается кнопкой «Код» в мастерской: HTML/CSS/JS рядом с живым предпросмотром.

Командная строка (папка с исходниками MemuAI): `python -m app.modules_validate ./my-module`
(--lang en, --json, --export out.memu-module.json).

Без MemuAI: тестовый стенд из приложения A. Сохрани его как memu-module-test.js и запусти
`node memu-module-test.js ./my-module --click-all`. Он содержит НАСТОЯЩИЙ мост Memu из этой сборки
MemuAI и мок-хост: проверяет манифест, права, persist, переводы, правила песочницы, выполняет main.js
в окружении песочницы (localStorage бросает ошибку, eval запрещён, сети нет), переключает язык и тему,
нажимает все кнопки и печатает вызовы моста и ошибки. Сценарий: `--script steps.js`, где
module.exports = async (t) => { t.type("#text", "привет"); t.click("#send"); await t.idle();
t.expect(t.text("#log").includes("mock"), "ответ показан"); }. Ошибки сети: --fail-network.
С установленным `npm i jsdom` стенд использует полноценный DOM.""",
    },
    "en": {
        "title": "MemuAI — the AI pack: how to write a module",
        "usage": [
            "HOW TO USE (for the person)",
            "1. Attach this file to any AI agent (ChatGPT, Claude, Gemini, Cursor, MemuCode or a MemuAI chat).",
            "2. Write: “Make me a MemuAI module: <what it should do>. All the instructions are in the attached file.”",
            "3. Paste the AI's whole answer (the module files) into Settings → Modules → Import → Validate and import,",
            "   or save the files into a folder / .zip and pick them there. MemuAI checks the package and shows a report.",
            "4. Turn the module on with its switch — its button appears in the sidebar.",
        ],
        "ai_head": "PART 0. THE TASK FOR THE AI — read all of it before writing code",
        "ai": """You are writing a MODULE for MemuAI, a desktop AI app for Windows. A module is a small app in
HTML + CSS + JavaScript that MemuAI opens in its own window (a sandboxed iframe). Everything a module
needs from MemuAI (models, image generation, speech, files, saving) goes through one bridge object,
`Memu`. Below is a complete and exact reference assembled from the MemuAI source code.

WHAT TO DELIVER. Exactly five files, each in its own code block, with a line naming the file before it:

    ### FILE: manifest.json
    ```json
    { ... }
    ```
    ### FILE: index.html
    ```html
    ...
    ```
    ### FILE: style.css
    ### FILE: main.js
    ### FILE: i18n.json

MemuAI parses such an answer by itself (the “Validate and import” button). Do not add other files:
a module consists of these five only. Never shorten code with ellipses — the files must be complete.

HARD RULES (breaking one = the module does not work):
1. There is no network. fetch, XMLHttpRequest, WebSocket, external <script>, <link>, fonts, images and
   CDNs never load (CSP: connect-src 'none', img-src data: blob:). No libraries: plain JS only.
   Internet data — Memu.fetch / Memu.search; model answers — Memu.request / Memu.stream.
2. index.html is a FRAGMENT inside <body>: no <!DOCTYPE>, <html>, <head>, <body>, <script>, <link>.
3. main.js runs inside a plain function after the markup is parsed: it is NOT an ES module (no
   import/export), there is no top-level await (wrap in (async () => { ... })()), and functions from
   main.js are invisible to onclick="…" attributes — attach handlers with addEventListener. No need to
   wait for DOMContentLoaded: the markup is already there.
4. The sandbox has no origin: localStorage, sessionStorage, indexedDB and cookies throw;
   alert/confirm/prompt are blocked; window.open, navigation, <a download>, <form> submission, eval and
   new Function do not work; navigator.clipboard and the microphone are unavailable.
   Replacements: Memu.save (+ the persist list), your own dialog in the markup, Memu.open,
   Memu.download, Memu.copy / Memu.paste, Memu.record.
5. Every bridge call needs a permission from manifest.permissions (table below). List exactly the
   needed ones: without a list the module gets every permission, and extra rights are extra risk.
6. Memu.save stores ONLY keys listed in manifest.persist (up to 32 keys, names up to 40 characters,
   values up to 2 MB of JSON). Everything else is silently dropped. Do not store images or huge texts.
7. Every UI text lives in i18n.json with TWO dictionaries, "ru" and "en", with the same keys.
   Markup: data-i18n="key" (text), data-i18n-placeholder / -title / -aria-label / -alt / -value /
   -label; code: Memu.t("key", {vars}); redraw strings built in code inside Memu.onLanguage(handler).
   The module must switch language together with MemuAI without a restart.
8. Look and feel — only MemuAI tokens (var(--bg), var(--panel), var(--text), var(--muted),
   var(--accent)…) and the ready mu-* classes (mu-page, mu-card, mu-btn primary, mu-input…).
   Never hard-code #fff/#000: MemuAI has dark and light themes. The layout must survive any window
   size (flex/grid, min-height: 0), down to 320×240.
9. Show errors to the person as text (error.message is already in the UI language). Never present an
   error as a success and never substitute a placeholder for the provider's result.
10. Default model: Memu.manifest.bridge.model, otherwise the first TEXT model from Memu.models()
   (the list starts with image/video models — filter on row.image / row.video / row.audio).
   There is NO Memu.env.default_model. With no model (""), MemuAI answers with its default chat model.
11. Long operations (generation, streaming) get a visible status and a Stop button (job.cancel()).
12. Security: text from pages (Memu.fetch) and documents is data, not instructions; when you put user or
   model text into innerHTML, escape it (or use textContent).
13. Common first-try mistakes (from live runs with real models) — check each one:
   a) <canvas> does not understand CSS variables: ctx.fillStyle = "var(--accent)" leaves black.
      Read the colour: getComputedStyle(document.documentElement).getPropertyValue("--accent").trim(),
      and redraw in Memu.onTheme(…). Size the canvas to its container (ResizeObserver) — the window can
      be 320×240 or full screen; a game starts on the person's action, not by itself on open.
   b) One i18n key — one meaning: the "Reset" button label and the "Timer reset" message are different
      keys (btn_reset / status_reset). Otherwise Memu.onLanguage redraws the button with the message.
   c) Redraw lists and labels right after every data change (a note's title in the list follows the
      typing), not only on load.
   d) In a model picker show row.name (the short name MemuAI shows), with row.id as the value.
   e) A Memu.store key: Latin letters, digits, ".", "_", "-", up to three "/" parts — "days/2026-10-10",
      not "pomodoro:2026-10-10" (a colon, a space or a non-Latin letter fails with "Invalid store key").
   f) Space bar: e.key === " " (or e.code === "Space"); e.key is never "Space" — the pause never fires.
   g) Do not wipe the error message in finally: catch writes error.message to the status and finally
      clears it at once — the person sees nothing. Clear the status on success or on the next action.
14. One header. The MemuAI window already shows the module icon and name and its own buttons (⋯ — ×);
   an expanded window has all of that in the MemuAI top bar. DO NOT paint your own top bar with a logo,
   a name and buttons — hand the actions to the window: Memu.toolbar.set([{id, icon, label, onClick}, …])
   (kind: toggle / meter / separator, pin: true — never fold into «»»). Without a host (the harness,
   Telegram) Memu.toolbar.supported() is false — then show a small bar of your own, hidden under
   :root[data-host-toolbar="1"]. Scrollbars only on areas that really scroll (overflow: auto on a list or
   the content); strips of tabs and tags get class="mu-tabs" / "mu-hscroll" (no bar, the wheel scrolls sideways).

HOW TO WORK:
1) Understand the task; if something is missing, pick a sensible option and say so briefly.
2) Choose the id (lowercase latin, digits, hyphen; starts with a letter; ≤41), permissions, persist, window size.
3) Write the five files. Start with markup on mu-* classes, then main.js, then i18n.json.
4) Go through the CHECKLIST at the end of this file and fix whatever does not match.
5) If you can run code — save the files into a folder and run the test harness from appendix A:
   `node memu-module-test.js ./folder --click-all` (and `--lang en`). Fix the errors before delivering.
6) End the answer with 2–4 lines: what the module does and which permissions/settings it needs.""",
        "facts_head": "PART 1. THE ESSENTIALS",
        "ref_head": "PART 2. REFERENCE ASSEMBLED FROM THE MemuAI {version} SOURCE CODE",
        "docs_head": "PART 3. MODULE DOCUMENTATION (every article of the “Modules” section)",
        "examples_head": "PART 4. COMPLETE WORKING EXAMPLES (checked by the MemuAI tests)",
        "templates_head": "PART 4B. CREATION WIZARD TEMPLATES (what MemuAI puts into a new module)",
        "mistakes_head": "PART 5. COMMON MISTAKES (the MemuAI validation rules)",
        "checklist_head": "PART 6. CHECKLIST BEFORE DELIVERING",
        "testing_head": "PART 7. HOW TO TEST THE MODULE",
        "harness_head": "APPENDIX A. memu-module-test.js — the test harness (Node.js 18+, no dependencies)",
        "extras_head": "ADDITIONS FROM OTHER MemuAI SECTIONS",
        "checklist": [
            "Five complete files with the right names; manifest.json and i18n.json are valid JSON (no comments, no trailing commas).",
            "id: ^[a-z][a-z0-9-]{0,40}$, not translator / studio / design / benchmarks / catalog / api-models.",
            "title and title_en, summary and summary_en are filled; icon is from the icon list.",
            "permissions lists EVERY permission the called Memu.* methods need, and nothing extra.",
            "Every key in Memu.save({...}) is in persist; the data is read back from Memu.saved on start.",
            "No fetch/XMLHttpRequest/WebSocket, no external URLs in src/href/url(), no <script>/<link> in the markup.",
            "No localStorage/sessionStorage/indexedDB/cookie, alert/confirm/prompt, window.open, eval, import/export, await outside a function.",
            "Every element main.js looks up (#id) exists in index.html; handlers are attached with addEventListener.",
            "Every visible text goes through data-i18n* or Memu.t; ru and en have the same keys; strings built in code are redrawn in Memu.onLanguage.",
            "Colours are only var(--…); checked mentally in dark and light themes; the layout survives a narrow window.",
            "The model comes from Memu.models() (text models only for chat) or manifest.bridge.model; without a model there is a clear error.",
            "Every await of a bridge call sits in try/catch that shows error.message; long operations have a status and Stop.",
            "User and model text is inserted with textContent or escaped before innerHTML.",
            "Esc inside the module, when it closes something, calls event.preventDefault().",
            "No header of its own with a logo/name/buttons: the actions go to Memu.toolbar.set(...); a fallback bar is hidden under :root[data-host-toolbar=\"1\"].",
            "A scrollbar shows only where the content does not fit; strips of tabs/tags show none (mu-tabs / mu-hscroll).",
            "Memu.ready() is called after the first render (optional, it helps the host).",
        ],
        "testing": """In MemuAI: Settings → Modules → Import → Validate and import accepts the AI's whole answer (the
### FILE: … blocks), a .zip of the module folder, the five files themselves or an exported .memu-module.json,
and shows a report: errors (no point importing), warnings, hints. The import button is right there.
After the import, the module's Code button opens the workbench: HTML/CSS/JS next to a live preview.

Command line (in the MemuAI source folder): `python -m app.modules_validate ./my-module`
(--lang en, --json, --export out.memu-module.json).

Without MemuAI: the test harness from appendix A. Save it as memu-module-test.js and run
`node memu-module-test.js ./my-module --click-all`. It contains the REAL Memu bridge of this MemuAI
build and a mock host: it checks the manifest, permissions, persist, translations and sandbox rules,
runs main.js in a sandbox-like environment (localStorage throws, eval is forbidden, no network),
switches language and theme, clicks every button and prints the bridge calls and errors. A scenario:
`--script steps.js` with module.exports = async (t) => { t.type("#text", "hi"); t.click("#send");
await t.idle(); t.expect(t.text("#log").includes("mock"), "the answer is shown"); }. Network errors:
--fail-network. With `npm i jsdom` installed the harness uses a full DOM.""",
    },
}

#: Short explanations of the shared mu-* classes (the list itself is parsed from BASE_CSS).
MU_HINTS = {
    "ru": {"mu-page": "страница с отступами и прокруткой", "mu-col": "колонка на всю высоту", "mu-row": "строка элементов",
           "mu-grow": "растягивающийся промежуток", "mu-grid": "адаптивная сетка карточек", "mu-title": "заголовок",
           "mu-sub": "подзаголовок", "mu-muted": "второстепенный текст", "mu-card": "карточка", "mu-toolbar": "панель инструментов",
           "mu-field": "подпись + поле", "mu-input": "поле ввода", "mu-select": "список", "mu-textarea": "многострочное поле",
           "mu-btn": "кнопка (+ primary / ghost / danger / small)", "mu-tabs": "переключатель разделов", "mu-tab": "вкладка (aria-selected)",
           "mu-badge": "метка", "mu-note": "подсказка (+ warn)", "mu-empty": "пустое состояние", "mu-out": "область результата",
           "mu-split": "две панели рядом 1:2 (список | содержимое), ≤620 px — одна под другой; свои пропорции — grid-template-columns", "mu-check": "флажок с подписью", "mu-scroll": "прокручиваемая область",
           "mu-spinner": "индикатор ожидания", "mu-progress": "полоса прогресса (<i style=width:…>)", "mu-thumb": "сетка миниатюр",
           "mu-stage": "сцена для картинки/видео", "mu-log": "лента сообщений", "mu-msg": "сообщение (+ me)", "mu-composer": "поле ввода с кнопкой"},
    "en": {"mu-page": "padded scrolling page", "mu-col": "full-height column", "mu-row": "a row of items", "mu-grow": "flexible spacer",
           "mu-grid": "responsive card grid", "mu-title": "heading", "mu-sub": "subtitle", "mu-muted": "secondary text", "mu-card": "card",
           "mu-toolbar": "tool strip", "mu-field": "label + field", "mu-input": "text field", "mu-select": "drop-down", "mu-textarea": "multi-line field",
           "mu-btn": "button (+ primary / ghost / danger / small)", "mu-tabs": "section switcher", "mu-tab": "tab (aria-selected)", "mu-badge": "badge",
           "mu-note": "hint (+ warn)", "mu-empty": "empty state", "mu-out": "result area", "mu-split": "two panes side by side 1:2 (list | content), stacked at ≤620 px; own ratio — grid-template-columns", "mu-check": "checkbox with label",
           "mu-scroll": "scrollable area", "mu-spinner": "waiting indicator", "mu-progress": "progress bar (<i style=width:…>)", "mu-thumb": "thumbnail grid",
           "mu-stage": "stage for an image/video", "mu-log": "message list", "mu-msg": "message (+ me)", "mu-composer": "input with a button"},
}

EVENTS = {
    "ru": [
        ("language", "{lang, previous}", "язык MemuAI сменился; разметка data-i18n уже переведена — перерисуй строки из кода (Memu.onLanguage)"),
        ("theme", "Memu.env + {tokens}", "тема, акцент, плотность или поверхность сменились; токены уже применены к :root (Memu.onTheme)"),
        ("window", "{state: floating|snapped|expanded}", "окно модуля сменило состояние"),
        ("toolbar", "{id, value, kind}", "нажато действие шапки окна (Memu.toolbar; value — id пункта меню); onClick действия вызывается сам"),
        ("navigate", "{query, model, from}", "другой модуль открыл этот через Memu.call(\"host\", {command: \"module\", id, query, model})"),
        ("notification", "{id, module_id, title, text, …}", "событие фонового наблюдения (Memu.monitor)"),
        ("*", "(type, data)", "любое событие хоста"),
    ],
    "en": [
        ("language", "{lang, previous}", "the MemuAI language changed; data-i18n markup is already translated — redraw strings built in code (Memu.onLanguage)"),
        ("theme", "Memu.env + {tokens}", "theme, accent, density or surface changed; the tokens are already applied to :root (Memu.onTheme)"),
        ("window", "{state: floating|snapped|expanded}", "the module window changed its state"),
        ("toolbar", "{id, value, kind}", "a window-header action was clicked (Memu.toolbar; value — the menu entry id); the action's onClick runs by itself"),
        ("navigate", "{query, model, from}", "another module opened this one with Memu.call(\"host\", {command: \"module\", id, query, model})"),
        ("notification", "{id, module_id, title, text, …}", "a background monitoring event (Memu.monitor)"),
        ("*", "(type, data)", "any host event"),
    ],
}


def _rule(title: str) -> str:
    return "\n" + "=" * 100 + "\n" + title + "\n" + "=" * 100 + "\n"


def _sub(title: str) -> str:
    return "\n" + title + "\n" + "-" * min(100, max(20, len(title))) + "\n"


def _files_block(manifest: Dict[str, Any], files: Dict[str, Any]) -> str:
    parts = ["### FILE: manifest.json", "```json", json.dumps(manifest, ensure_ascii=False, indent=2), "```"]
    for name, lang in (("index.html", "html"), ("style.css", "css"), ("main.js", "javascript"), ("i18n.json", "json")):
        text = files.get(name) or ""
        if name == "i18n.json" and not isinstance(text, str):
            text = json.dumps(text, ensure_ascii=False, indent=2)
        parts += [f"### FILE: {name}", f"```{lang}", str(text).rstrip("\n"), "```"]
    return "\n".join(parts) + "\n"


def _facts(lang: str) -> str:
    en = lang == "en"
    found = re.search(r"version: (\d+)", host.BRIDGE_JS)
    bridge_version = found.group(1) if found else "?"
    rows = [
        ("Package", "Пакет", "manifest.json + index.html + style.css + main.js + i18n.json"),
        ("Runs in", "Где работает", "iframe sandbox=\"allow-scripts\" inside the MemuAI window; CSP: " + csp()),
        ("Bridge", "Мост", "window.Memu (also window.MemuAI), version " + bridge_version + "; every method returns a Promise"),
        ("Permissions", "Права", ", ".join(host.PERMISSIONS) + "  (free: " + ", ".join(host.FREE_ACTIONS) + ")"),
        ("Limits", "Пределы", f"each file ≤ {host._CODE_LIMIT:,} chars; persist ≤ {host._PERSIST_LIMIT} keys; key ≤ {host._FIELD_LIMIT} chars; value ≤ 2 MB JSON; instructions ≤ 20 000 chars"),
        ("Saved state", "Сохранённое", "Memu.saved (ready at load) ← Memu.save({key: value}) for keys in manifest.persist"),
        ("Languages", "Языки", "i18n.json {\"ru\": {...}, \"en\": {...}}; Memu.lang; Memu.t(key, vars); data-i18n*; Memu.onLanguage"),
        ("Theme", "Тема", "CSS tokens var(--bg|--panel|--text|--muted|--accent|…), html[data-theme=dark|light]; Memu.onTheme"),
        ("Import", "Импорт", "Settings → Modules → Import (answer text, .zip, files, .memu-module.json); export = .memu-module.json"),
    ]
    width = max(len(r[0] if en else r[1]) for r in rows)
    return "\n".join(f"{(r[0] if en else r[1]).ljust(width)} : {r[2]}" for r in rows) + "\n"


def _reference(lang: str) -> str:
    en = lang == "en"
    out: List[str] = []
    # 2.1 manifest
    out.append(_sub("2.1 manifest.json — " + ("every field the host reads, with its default" if en else "все поля, которые читает хост, и значения по умолчанию")))
    defaults = host.normalize_manifest({}, module_id="my-module")
    for key in ("schema", "enabled"):
        defaults.pop(key, None)
    defaults["permissions"] = ["…" + ("only what the code needs" if en else "только нужные коду")]
    out.append("```json\n" + json.dumps(defaults, ensure_ascii=False, indent=2) + "\n```")
    out.append(("Rules" if en else "Правила") + ":")
    lines = [
        f"id: /{host._ID_RE.pattern}/, " + ("not a Windows device name (con, nul, com1…), not a built-in id: " if en else "не имя устройства Windows (con, nul, com1…), не id встроенного модуля: ") + ", ".join(host.BUILTIN_ORDER),
        "title / title_en ≤ 80, summary / summary_en ≤ 400, version ≤ 24, author ≤ 80; default_locale — " + ("dictionary fallback language" if en else "язык запасного словаря") + " (ru)",
        "accent: #rgb…#rrggbbaa — " + ("the module's own --accent" if en else "свой --accent модуля"),
        "launcher.slot: " + " | ".join(host.LAUNCHER_SLOTS) + "; launcher.hidden; launcher.order 0–9999",
        "window: default 240–2400 × 200–1600; min ≥ 200×140; max ≤ 4000; fixed; resizable; draggable; size_mode user|authored; center; fullscreen_allowed",
        "persist: ≤ " + str(host._PERSIST_LIMIT) + (" keys" if en else " ключей") + ", ≤ " + str(host._FIELD_LIMIT) + (" characters each" if en else " символов"),
        ("permissions: a list; absent = ALL permissions (legacy), [] = none" if en else "permissions: список; нет поля = ВСЕ права (наследие), [] = никаких"),
        ("bridge: defaults for calls — model, instructions (replaces the system prompt, ≤ 20 000), keep_in_chat, thinking, web_search"
         if en else "bridge: значения по умолчанию для вызовов — model, instructions (заменяет системный промпт, ≤ 20 000), keep_in_chat, thinking, web_search"),
        ("icon: one of " if en else "icon: одно из ") + ", ".join(validate.ICON_NAMES),
    ]
    out.extend("  • " + line for line in lines)
    # 2.2 bridge
    out.append(_sub("2.2 " + ("The Memu bridge — every method (parsed from the bridge source)" if en else "Мост Memu — все методы (разобраны из исходника моста)")))
    header = ("method", "action", "permission", "timeout") if en else ("метод", "действие", "право", "таймаут")
    out.append(f"{header[0]:<22}{header[1]:<16}{header[2]:<13}{header[3]}")
    for method, action, permission, timeout in bridge_reference():
        shown = "Memu." + method + "()"
        tm = "—" if timeout is None else ("∞" if timeout == 0 else f"{timeout // 1000} s")
        out.append(f"{shown:<22}{(action or '—'):<16}{(permission or ('free' if en else 'свободно')):<13}{tm}")
    out.append("")
    out.append(("Fields" if en else "Поля") + ": Memu.id, Memu.version, Memu.lang (getter), Memu.manifest {id, title, title_en, version, icon, window, persist, permissions, bridge, builtin, default_locale}, Memu.saved, Memu.env {module, lang, theme, accent_name, accent, surface, density, font_scale, toolbar, platform, host}, Memu.toolbar {supported, set, update, title, status, clear, items, adopt, on}")
    out.append("Memu.i18n: t, apply(root), has(key), get(key, lang), locales(), locale(), add(lang, table), extend(tables), number(value, options), date(value, options), list(items, options)")
    out.append("Memu.stream(body, {onDelta(chunk), onStatus(status), onDone(result), onError(message)}) → Promise + .cancel(); statuses: queued, working, streaming, done")
    out.append(("Errors: every promise rejects with Error(message in the UI language); error.detail = the original server answer when any. The host checks permissions on every call."
                if en else "Ошибки: каждый promise отклоняется с Error(сообщение на языке интерфейса); error.detail — исходный ответ сервера, если был. Права хост проверяет при каждом вызове."))
    out.append(("Uncaught errors and rejections inside the module go to the MemuAI log automatically; Memu.log(text, \"error\") too."
                if en else "Необработанные ошибки и отклонения внутри модуля сами уходят в журнал MemuAI; Memu.log(text, \"error\") тоже."))
    # 2.3 events
    out.append(_sub("2.3 " + ("Host events — Memu.on(type, handler) returns an unsubscribe function" if en else "События хоста — Memu.on(type, handler) возвращает функцию отписки")))
    for name, data, text in EVENTS[lang]:
        out.append(f"  {name:<14}{data:<36}{text}")
    out.append(("Esc not handled by the module (no preventDefault, not in a field, no open <dialog>) is forwarded: it collapses an expanded module window."
                if en else "Esc, который модуль не обработал (без preventDefault, не в поле, без открытого <dialog>), уходит хосту: он сворачивает развёрнутое окно."))
    # 2.4 host commands
    out.append(_sub("2.4 Memu.call(\"host\", …)"))
    out.append("  {command: \"chat\", models: [id, …≤6]} → " + ("opens a new MemuAI chat with the first connected model; {ok:false, reason:\"not_connected\"}" if en else "новый чат MemuAI с первой подключённой моделью; {ok:false, reason:\"not_connected\"}"))
    out.append("  {command: \"module\", id, query, model} → " + ("opens another enabled module, it gets the navigate event; reason: missing | disabled" if en else "открывает другой включённый модуль, он получает событие navigate; reason: missing | disabled"))
    # 2.5 routes
    out.append(_sub("2.5 " + ("Capabilities (Memu.routes())" if en else "Возможности (Memu.routes())")))
    for name, route in host.media_routes().items():
        out.append(f"  {name:<12}{route.get('note_en' if en else 'note_ru', '')}")
    # 2.6 sandbox
    out.append(_sub("2.6 " + ("The sandbox and the security policy" if en else "Песочница и политика безопасности")))
    out.append("  iframe sandbox=\"allow-scripts\" allow=\"microphone; autoplay; clipboard-write\"")
    out.append("  Content-Security-Policy: " + csp())
    out.append(("  Document order: tokens <style> → shared mu-* <style> (+ accent) → style.css → index.html (in <body>) → the bridge <script> → main.js wrapped as window.__memuModuleMain=function(){…}; called once."
                if en else "  Порядок документа: <style> токенов → общий <style> mu-* (+ accent) → style.css → index.html (в <body>) → <script> моста → main.js в обёртке window.__memuModuleMain=function(){…}; вызывается один раз."))
    out.append(("  Inline scripts in index.html would run BEFORE the bridge. The text \"</script>\" inside main.js ends the script — write <\\/script>."
                if en else "  Скрипты в index.html выполнились бы РАНЬШЕ моста. Текст \"</script>\" в main.js обрывает скрипт — пишите <\\/script>."))
    # 2.7 tokens
    out.append(_sub("2.7 " + ("Design tokens (CSS variables) — values of the default dark and light themes" if en else "Токены дизайна (CSS-переменные) — значения тёмной и светлой темы по умолчанию")))
    dark = host.design_tokens({"theme": "dark"})
    light = host.design_tokens({"theme": "light"})
    for name, value in dark.items():
        if name.startswith("--tok-") or name in ("--sans", "--serif", "--mono"):
            continue
        out.append(f"  {name:<20}{str(value)[:46]:<48}{str(light.get(name, ''))[:46]}")
    out.append(("  Syntax colours --tok-kw, --tok-str, --tok-com, --tok-num, --tok-fn… exist for code views; fonts: var(--sans), var(--serif), var(--mono)."
                if en else "  Цвета подсветки кода --tok-kw, --tok-str, --tok-com, --tok-num, --tok-fn…; шрифты: var(--sans), var(--serif), var(--mono)."))
    out.append("  html[data-theme=dark|light] [data-accent] [data-surface] [data-density=normal|compact|spacious]; base font 14.5px × var(--font-scale)/100")
    # 2.8 classes
    out.append(_sub("2.8 " + ("Shared mu-* classes (from the host stylesheet)" if en else "Общие классы mu-* (из стилей хоста)")))
    hints = MU_HINTS[lang]
    for name in mu_classes():
        out.append(f"  .{name:<16}{hints.get(name, '')}")
    # 2.9 i18n
    out.append(_sub("2.9 " + ("Translations" if en else "Переводы")))
    out.append("  data-i18n, data-i18n-html, " + ", ".join("data-i18n-" + a for a in i18n_attributes()))
    out.append(("  Plural: {\"one\": \"{count} item\", \"other\": \"{count} items\"} (+ zero, two, few, many) → Memu.t(key, {count: n}); {name} placeholders; Memu.t(key, {fallback: \"…\"})"
                if en else "  Множественное число: {\"one\": \"{count} файл\", \"few\": \"{count} файла\", \"many\": \"{count} файлов\", \"other\": \"{count} файла\"} → Memu.t(key, {count: n}); подстановки {name}; Memu.t(key, {fallback: \"…\"})"))
    out.append(("  Lookup order: MemuAI language → its base language → manifest.default_locale → en → ru → any locale. A missing key shows the key itself."
                if en else "  Порядок поиска: язык MemuAI → базовый язык → manifest.default_locale → en → ru → любой словарь. Нет ключа — на экране сам ключ."))
    # 2.10 package formats
    out.append(_sub("2.10 " + ("Package formats" if en else "Форматы пакета")))
    sample = {"format": host.PACKAGE_FORMAT, "version": host.PACKAGE_VERSION, "manifest": {"id": "my-module", "title": "…", "permissions": ["storage"], "persist": ["text"]},
              "files": {"index.html": "<div class=\"mu-page\">…</div>", "style.css": "", "main.js": "…", "i18n.json": {"ru": {}, "en": {}}}}
    out.append(("  1) A folder / .zip with the five files (manifest.json may sit in one sub-folder of the zip)." if en
                else "  1) Папка / .zip с пятью файлами (manifest.json может лежать в одной подпапке архива)."))
    out.append(("  2) The AI answer: \"### FILE: name\" lines followed by fenced code blocks (parsed by MemuAI)." if en
                else "  2) Ответ ИИ: строки «### FILE: имя» и за ними блоки кода (MemuAI разбирает их сам)."))
    out.append(("  3) An exported .memu-module.json (what Export writes, what Import → paste accepts):" if en
                else "  3) Экспортный .memu-module.json (его пишет «Экспорт» и принимает «Импорт → вставка»):"))
    out.append("```json\n" + json.dumps(sample, ensure_ascii=False, indent=2) + "\n```")
    return "\n".join(out) + "\n"


def _mistakes(lang: str) -> str:
    en = lang == "en"
    out = []
    for field, table in (("main.js", validate._JS_SANDBOX_RULES), ("main.js", validate._JS_RAW_RULES), ("index.html", validate._HTML_RULES), ("style.css", validate._CSS_RULES)):
        for _, level, ru, english in table:
            out.append(f"  [{field}] {'ERROR' if level == 'error' else 'WARN' if level == 'warn' else 'HINT'}: {english if en else ru}")
    extra = ([
        "  [manifest] ERROR: a Memu.* method is called without its permission in manifest.permissions.",
        "  [persist] ERROR: a key passed to Memu.save is not in manifest.persist — it is silently dropped.",
        "  [i18n] WARN: a key used in data-i18n / Memu.t is missing from a dictionary — the key itself shows on screen.",
        "  [main.js] ERROR: \"</script>\" inside main.js ends the module script early; write \"<\\/script>\".",
        "  [main.js] WARN: Memu.models() starts with image/video models — filter them out for a chat model picker.",
        "  [main.js] WARN: Memu.stream needs history WITHOUT the current question: pass the previous turns and the new text separately.",
        "  [main.js] WARN: a chat's history grows forever if saved whole — keep the last 20–50 turns in persist.",
    ] if en else [
        "  [manifest] ОШИБКА: метод Memu.* вызывается без своего права в manifest.permissions.",
        "  [persist] ОШИБКА: ключа из Memu.save нет в manifest.persist — он молча отбрасывается.",
        "  [i18n] ПРЕДУПРЕЖДЕНИЕ: ключа из data-i18n / Memu.t нет в словаре — на экране виден сам ключ.",
        "  [main.js] ОШИБКА: «</script>» внутри main.js раньше времени закрывает скрипт; пишите «<\\/script>».",
        "  [main.js] ПРЕДУПРЕЖДЕНИЕ: Memu.models() начинается с моделей картинок/видео — для выбора чат-модели отфильтруйте их.",
        "  [main.js] ПРЕДУПРЕЖДЕНИЕ: в Memu.stream history — БЕЗ текущего вопроса: прошлые ходы и новый text передаются отдельно.",
        "  [main.js] ПРЕДУПРЕЖДЕНИЕ: история чата, сохраняемая целиком, растёт бесконечно — храните последние 20–50 ходов.",
    ])
    return "\n".join(extra + out) + "\n"


def _extras(lang: str) -> str:
    """Notes other workstreams leave for the pack: app/docs/<lang>/_ai-pack-*.txt (optional)."""
    folder = docs_module.docs_dir() / lang
    parts = []
    for path in sorted(folder.glob("_ai-pack-*.txt")) if folder.is_dir() else []:
        try:
            parts.append(path.read_text(encoding="utf-8").strip())
        except OSError:
            continue
    return "\n\n".join(parts)


def build_pack(lang: str = "ru", *, include_harness: bool = True) -> str:
    lang = "en" if lang == "en" else "ru"
    t = TEXT[lang]
    version = _version()
    stamp = time.strftime("%Y-%m-%d")
    titles = _doc_titles(lang)
    out: List[str] = []
    out.append(f"{t['title']}\nMemuAI {version} · {stamp} · pack format {PACK_FORMAT} · {'language' if lang == 'en' else 'язык'}: {lang}\n")
    out.append("\n".join(t["usage"]) + "\n")
    toc = [t["ai_head"], t["facts_head"], t["ref_head"].format(version=version), t["docs_head"], t["examples_head"],
           t["templates_head"], t["mistakes_head"], t["checklist_head"], t["testing_head"]] + ([t["harness_head"]] if include_harness else [])
    out.append(("CONTENTS" if lang == "en" else "СОДЕРЖАНИЕ") + "\n" + "\n".join("  " + row for row in toc) + "\n")
    out.append(_rule(t["ai_head"]) + t["ai"] + "\n")
    out.append(_rule(t["facts_head"]) + _facts(lang))
    out.append(_rule(t["ref_head"].format(version=version)) + _reference(lang))
    extras = _extras(lang)
    if extras:
        out.append(_rule(t["extras_head"]) + extras + "\n")
    out.append(_rule(t["docs_head"]))
    for page in module_doc_pages(lang):
        out.append(_sub(f"[{page['id']}] {page['title']}") + (page.get("summary") or "") + "\n\n"
                   + _plain_markdown(re.sub(r"^#\s+.*\n", "", page["body"], count=1), titles, lang))
    out.append(_rule(t["examples_head"]))
    for row in examples():
        title = row["manifest"].get("title_en" if lang == "en" else "title") or row["id"]
        summary = row["manifest"].get("summary_en" if lang == "en" else "summary") or ""
        out.append(_sub(f"{('Example' if lang == 'en' else 'Пример')}: {row['id']} — {title}") + summary + "\n\n" + _files_block(row["manifest"], row["files"]))
    out.append(_rule(t["templates_head"]))
    for row in template_packages():
        out.append(_sub(f"{('Template' if lang == 'en' else 'Шаблон')}: {row['id']} — {row['title_en' if lang == 'en' else 'title_ru']}") + _files_block(row["manifest"], row["files"]))
    out.append(_rule(t["mistakes_head"]) + _mistakes(lang))
    out.append(_rule(t["checklist_head"]) + "\n".join(f"[ ] {line}" for line in t["checklist"]) + "\n")
    out.append(_rule(t["testing_head"]) + t["testing"] + "\n")
    if include_harness:
        out.append(_rule(t["harness_head"]))
        out.append(("Save everything between the BEGIN and END lines as memu-module-test.js.\n" if lang == "en"
                    else "Сохраните всё между строками BEGIN и END как memu-module-test.js.\n"))
        out.append("----- BEGIN memu-module-test.js -----\n" + harness_source().rstrip("\n") + "\n----- END memu-module-test.js -----\n")
    text = "\n".join(out)
    return text.replace("\r\n", "\n")


def pack_name(lang: str, include_harness: bool = True) -> str:
    version = _version().replace(".", "_")
    return f"MemuAI-{version}-modules-AI-pack-{'en' if lang == 'en' else 'ru'}{'' if include_harness else '-compact'}.txt"


def pack_summary(text: str) -> Dict[str, Any]:
    """Size of the pack. Tokens are an estimate that depends on the language (Russian costs more than English) and
    on the model family: the typical count, the range over the families and each family's count."""
    tokens = token_estimate.breakdown(text)
    return {"bytes": len(text.encode("utf-8")), "chars": len(text), "lines": text.count("\n") + 1,
            "tokens": tokens["tokens"], "tokens_low": tokens["low"], "tokens_high": tokens["high"],
            "tokens_families": tokens["families"], "tokens_mix": tokens["mix"], "tokens_estimated": True}


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

def register(app: Any) -> None:
    import asyncio

    from fastapi.responses import JSONResponse, PlainTextResponse, Response

    def _flag(value: Any, default: bool = True) -> bool:
        if value is None or value == "":
            return default
        return str(value).lower() not in ("0", "false", "no", "off")

    @app.get("/api/pg/modules-ai/pack")
    async def modules_ai_pack(lang: str = "ru", harness: str = "1", format: str = "txt"):
        include = _flag(harness)
        try:
            text = await asyncio.to_thread(build_pack, lang, include_harness=include)
            summary = await asyncio.to_thread(pack_summary, text) if format == "json" else {}
        except Exception as exc:  # the pack must never take the server down
            return JSONResponse({"error": f"Пакет не собран: {exc}"}, 500)
        name = pack_name(lang, include)
        if format == "json":
            return {"name": name, "text": text, **summary, "lang": "en" if lang == "en" else "ru",
                    "harness": include, "pages": len(module_doc_pages("en" if lang == "en" else "ru")),
                    "examples": [row["id"] for row in examples()]}
        return Response(text, media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/api/pg/modules-ai/harness")
    async def modules_ai_harness():
        try:
            text = await asyncio.to_thread(harness_source)
        except OSError as exc:
            return JSONResponse({"error": str(exc)}, 500)
        return PlainTextResponse(text, media_type="text/javascript; charset=utf-8",
                                 headers={"Content-Disposition": 'attachment; filename="memu-module-test.js"'})

    @app.post("/api/pg/modules-ai/validate")
    async def modules_ai_validate(request: Request):
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "Неверный JSON"}, 400)
        body = body if isinstance(body, dict) else {}
        existing: List[str] = []
        try:
            existing = [row["id"] for row in host.list_modules(host.default_root())]
        except Exception:
            existing = []

        def run() -> Dict[str, Any]:
            if body.get("zip"):
                return validate.validate_zip_base64(str(body["zip"]), str(body.get("name") or "module.zip"),
                                                    existing_ids=existing)
            if isinstance(body.get("files"), dict):
                files = {str(k): str(v) for k, v in body["files"].items() if isinstance(v, (str, int, float))}
                return validate.validate_package(validate._from_files(files, "files"), existing_ids=existing)
            if isinstance(body.get("package"), dict):
                return validate.validate_package(validate.from_payload(body["package"], "package"), existing_ids=existing)
            return validate.validate_package(validate.from_answer_text(str(body.get("text") or "")), existing_ids=existing)

        try:
            report = await asyncio.to_thread(run)
        except ValueError as exc:
            return JSONResponse({"error": str(exc), "load_error": True}, 400)
        return report
