# -*- coding: utf-8 -*-
"""Validate a MemuAI module package before importing it (Round 29).

A package can come as

* a folder with ``manifest.json``, ``index.html``, ``style.css``, ``main.js`` and an optional
  ``i18n.json`` (what an AI agent usually writes),
* a ``.zip`` of such a folder (the files may sit in one sub-folder inside the archive),
* an exported ``*.memu-module.json`` (``{"format": "memu-module", "manifest": …, "files": …}``).

:func:`validate_package` returns a report — errors (the import would fail or the module would not
work), warnings (it works but something is wrong) and hints — plus the package normalized to the
export format, ready for ``POST /api/pg/modules/import``. Every rule comes from the real host:
``modules_host`` limits, permissions and the bridge source (:data:`modules_host.BRIDGE_JS`), and the
sandbox the module document runs in (``sandbox="allow-scripts"`` + its Content-Security-Policy).

CLI::

    python -m app.modules_validate path/to/module            # folder, .zip or .memu-module.json
    python -m app.modules_validate path --lang en --json
    python -m app.modules_validate path --export out.memu-module.json
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

from . import modules_host as host

PACKAGE_FILES = ("manifest.json", "index.html", "style.css", "main.js", "i18n.json")
MAX_ARCHIVE = 8 * 1024 * 1024          # a module is text: 4 × 400 000 characters fit easily
MAX_ENTRY = 2 * 1024 * 1024

#: Manifest keys the host reads (normalize_manifest / normalize_window); anything else is ignored.
MANIFEST_KEYS = ("id", "title", "title_en", "summary", "summary_en", "version", "author", "icon", "accent",
                 "icon_bg", "default_locale", "launcher", "window", "persist", "permissions", "bridge", "schema",
                 "enabled", "created", "builtin", "html", "css", "js", "i18n", "net_import", "contributes")
WINDOW_KEYS = ("default_width", "default_height", "width", "height", "min_width", "min_height", "max_width",
               "max_height", "fixed", "resizable", "draggable", "size_mode", "center", "fullscreen_allowed")
BRIDGE_KEYS = ("model", "instructions", "keep_in_chat", "thinking", "web_search")
LAUNCHER_KEYS = ("hidden", "slot", "order")


# --------------------------------------------------------------------------
# The bridge, read from its own source
# --------------------------------------------------------------------------

def bridge_methods() -> Dict[str, str]:
    """``Memu.<method>`` → bridge action, parsed from ``modules_host.BRIDGE_JS``.

    ``record.start/stop/cancel`` map to ``record``; ``stream`` posts its own message.
    """
    source = host.BRIDGE_JS
    start = source.find("var Memu = {")
    body = source[start:source.find("window.Memu = Memu", start)]
    methods: Dict[str, str] = {}
    for match in re.finditer(r"(\w+): function \([^)]*\) \{\s*return call\(\"(\w+)\"", body):
        name, action = match.group(1), match.group(2)
        methods["record." + name if name in ("start", "stop", "cancel") else name] = action
    if "stream: function" in body:
        methods["stream"] = "stream"
    # Round 29: streams opened with openStream("complete", …) and the Memu.store.* namespace.
    for match in re.finditer(r"(\w+): function \([^)]*\) \{\s*return openStream\(\"(\w+)\"", body):
        methods[match.group(1)] = match.group(2)
    at = body.find("store: {")
    store = body[at:body.find("\n    },", at)] if at >= 0 else ""
    for name in re.findall(r"\n      (\w+): function", store):
        methods["store." + name] = "store"
        if methods.get(name) == "store":   # the nested `list:`/`usage:` matched the generic pattern above
            del methods[name]
    for name in re.findall(r"\n    (\w+): function", body):
        methods.setdefault(name, "")
    # Round 30: Memu.toolbar.* — the window's single header (no permission needed).
    at = source.find("var toolbar = {")
    toolbar = source[at:source.find("\n  };", at)] if at >= 0 else ""
    for name in re.findall(r"\n    (\w+): (?:function|toolbarSupported)", toolbar):
        methods["toolbar." + name] = "toolbar" if name not in ("supported", "items", "on") else ""
    for name in ("id", "version", "manifest", "saved", "env", "lang", "i18n", "record", "call", "t", "toolbar"):
        methods.setdefault(name, "")
    return methods


def method_permissions() -> Dict[str, str]:
    """``Memu.<method>`` → the permission it needs ("" when it is free)."""
    return {name: host.ACTION_PERMISSION.get(action, "") for name, action in bridge_methods().items()}


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def _decode(raw: bytes, name: str) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{name}: not UTF-8 text ({exc.reason})") from exc


def _from_files(files: Dict[str, str], origin: str) -> Dict[str, Any]:
    """{file name: text} of a folder → a spec like an exported package."""
    names = {PurePosixPath(name).name.lower(): name for name in files}
    if "manifest.json" not in names:
        if "package.json" in names or any(n.endswith(".memu-module.json") for n in names):
            key = names.get("package.json") or next(n for n in names if n.endswith(".memu-module.json"))
            return _from_json_text(files[names.get(key, key)], origin)
        raise ValueError("manifest.json is missing")
    try:
        manifest = json.loads(files[names["manifest.json"]] or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"manifest.json: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json must be an object")
    i18n_text = files.get(names.get("i18n.json", ""), "")
    return {"origin": origin, "manifest": manifest,
            "files": {"index.html": files.get(names.get("index.html", ""), ""),
                      "style.css": files.get(names.get("style.css", ""), ""),
                      "main.js": files.get(names.get("main.js", ""), ""),
                      "i18n.json": i18n_text},
            "extra_files": sorted(name for name in files if PurePosixPath(name).name.lower() not in PACKAGE_FILES
                                  and not PurePosixPath(name).name.startswith(".") and not name.endswith(".lock"))}


def _from_json_text(text: str, origin: str) -> Dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
    return from_payload(payload, origin)


def from_payload(payload: Any, origin: str = "package") -> Dict[str, Any]:
    """An exported package object (or a bare manifest with html/css/js inside)."""
    if not isinstance(payload, dict):
        raise ValueError("the package must be a JSON object")
    manifest = payload.get("manifest") if isinstance(payload.get("manifest"), dict) else dict(payload)
    files = payload.get("files") if isinstance(payload.get("files"), dict) else {}
    i18n = files.get("i18n.json", manifest.get("i18n", ""))
    out_files = {"index.html": str(files.get("index.html", manifest.get("html", "")) or ""),
                 "style.css": str(files.get("style.css", manifest.get("css", "")) or ""),
                 "main.js": str(files.get("main.js", manifest.get("js", "")) or ""),
                 "i18n.json": i18n if isinstance(i18n, str) else json.dumps(i18n or {}, ensure_ascii=False)}
    for key in ("html", "css", "js", "i18n"):
        manifest.pop(key, None)
    fmt = str(payload.get("format") or "")
    return {"origin": origin, "manifest": manifest, "files": out_files, "format": fmt,
            "extra_files": [], "package_version": payload.get("version")}


_ANSWER_FILE = re.compile(r"(?<![\w.-])(manifest\.json|index\.html|style\.css|main\.js|i18n\.json)(?![\w.-])", re.I)


def from_answer_text(text: str, origin: str = "answer") -> Dict[str, Any]:
    """An AI answer: "### FILE: manifest.json" + a fenced block per file — or a JSON package.

    The file name may stand on the line before the fence (as a heading, in bold or in backticks) or in
    the fence info string (```json manifest.json). A pasted JSON package or bare manifest works too.
    """
    raw = str(text or "").replace("\r\n", "\n").strip()
    if not raw:
        raise ValueError("nothing to check: paste the AI answer or a package")
    if raw.startswith("{"):
        return _from_json_text(raw, origin)
    lines = raw.split("\n")
    files: Dict[str, str] = {}
    i = 0
    while i < len(lines):
        fence = re.match(r"^\s*(`{3,}|~{3,})\s*([^`]*)$", lines[i])
        if not fence:
            i += 1
            continue
        marker, info = fence.group(1), fence.group(2)
        name_match = _ANSWER_FILE.search(info)
        if not name_match:
            for back in range(1, 4):
                if i - back < 0:
                    break
                prev = lines[i - back].strip()
                if not prev:
                    continue
                name_match = _ANSWER_FILE.search(prev)
                break
        body: List[str] = []
        j = i + 1
        while j < len(lines) and not re.match(r"^\s*" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}\s*$", lines[j]):
            body.append(lines[j])
            j += 1
        content = "\n".join(body)
        if name_match:
            name = name_match.group(1).lower()
            files.setdefault(name, content + "\n")
        elif content.lstrip().startswith("{") and '"memu-module"' in content and not files:
            return _from_json_text(content, origin)
        i = j + 1
    if "manifest.json" not in files:
        raise ValueError("no manifest.json block found: each file must be a code block with its name on the line above"
                         " (### FILE: manifest.json)")
    return _from_files(files, origin)


def from_zip_bytes(raw: bytes, origin: str = "module.zip") -> Dict[str, Any]:
    if len(raw) > MAX_ARCHIVE:
        raise ValueError(f"the archive is larger than {MAX_ARCHIVE // (1024 * 1024)} MB")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ValueError("not a ZIP archive") from exc
    with archive:
        entries = [info for info in archive.infolist() if not info.is_dir()]
        for info in entries:
            parts = PurePosixPath(info.filename.replace("\\", "/")).parts
            if info.filename.startswith(("/", "\\")) or ".." in parts or re.match(r"^[A-Za-z]:", info.filename):
                raise ValueError(f"unsafe path inside the archive: {info.filename}")
        manifests = [info for info in entries if PurePosixPath(info.filename).name.lower() == "manifest.json"
                     and "__MACOSX" not in info.filename]
        if not manifests:
            jsons = [info for info in entries if info.filename.lower().endswith(".memu-module.json")]
            if len(jsons) == 1:
                return _from_json_text(_decode(archive.read(jsons[0]), jsons[0].filename), origin)
            raise ValueError("manifest.json is missing in the archive")
        manifests.sort(key=lambda info: info.filename.count("/"))
        base = str(PurePosixPath(manifests[0].filename).parent)
        base = "" if base == "." else base + "/"
        files: Dict[str, str] = {}
        total = 0
        for info in entries:
            if not info.filename.startswith(base) or "__MACOSX" in info.filename:
                continue
            relative = info.filename[len(base):]
            if info.file_size > MAX_ENTRY:
                if PurePosixPath(relative).name.lower() in PACKAGE_FILES:
                    raise ValueError(f"{relative} is larger than {MAX_ENTRY // (1024 * 1024)} MB")
                files[relative] = ""
                continue
            total += info.file_size
            if total > MAX_ARCHIVE * 2:
                raise ValueError("the archive unpacks to too much data")
            data = archive.read(info)
            if PurePosixPath(relative).name.lower() in PACKAGE_FILES:
                files[relative] = _decode(data, relative)
            else:
                files[relative] = ""
        return _from_files(files, origin)


def load_source(path: Any) -> Dict[str, Any]:
    """A folder, a ZIP archive or a JSON package on disk."""
    target = Path(path)
    if target.is_dir():
        files = {}
        for item in sorted(target.rglob("*")):
            if item.is_file() and ".git" not in item.parts and item.stat().st_size <= MAX_ENTRY:
                relative = item.relative_to(target).as_posix()
                if item.name.lower() in PACKAGE_FILES and relative.count("/") == 0:
                    files[relative] = _decode(item.read_bytes(), relative)
                elif relative.count("/") == 0:
                    files[relative] = ""
        return _from_files(files, str(target))
    if not target.is_file():
        raise ValueError(f"not found: {target}")
    raw = target.read_bytes()
    if raw[:2] == b"PK" or target.suffix.lower() == ".zip":
        return from_zip_bytes(raw, target.name)
    if target.name.lower() == "manifest.json":
        return load_source(target.parent)
    return _from_json_text(_decode(raw, target.name), target.name)


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

def _regex_allowed(out: List[str]) -> bool:
    """Whether a "/" here starts a regular expression literal (not a division)."""
    text = "".join(out[-40:]).rstrip()
    if not text:
        return True
    if text[-1] in "(,=:[!&|?{};+-*%<>~^":
        return True
    return bool(re.search(r"(?:^|[^\w$])(?:return|typeof|case|do|else|in|of|new|delete|void|throw|yield|await)$", text))


def _strip_js_comments_and_strings(js: str) -> str:
    """Code with comments removed and string/regex bodies blanked, for the pattern checks."""
    out: List[str] = []
    i, n = 0, len(js)
    while i < n:
        ch = js[i]
        nxt = js[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            end = js.find("\n", i)
            i = n if end < 0 else end
            continue
        if ch == "/" and nxt == "*":
            end = js.find("*/", i + 2)
            stop = n if end < 0 else end + 2
            out.append("\n" * js.count("\n", i, stop))      # keep line numbers right
            i = stop
            continue
        if ch == "/" and _regex_allowed(out):
            # a regex literal: skip to the closing "/" outside a [...] class
            j, in_class = i + 1, False
            while j < n and js[j] != "\n":
                if js[j] == "\\":
                    j += 2
                    continue
                if js[j] == "[":
                    in_class = True
                elif js[j] == "]":
                    in_class = False
                elif js[j] == "/" and not in_class:
                    break
                j += 1
            if j < n and js[j] == "/":
                out.append("/r/")
                i = j + 1
                while i < n and js[i].isalpha():
                    i += 1
                continue
        if ch in "\"'`":
            quote = ch
            out.append(ch)
            i += 1
            while i < n and js[i] != quote:
                if js[i] == "\\":
                    i += 2
                    continue
                if quote == "`" and js.startswith("${", i):
                    depth = 1
                    start = i + 2
                    i += 2
                    while i < n and depth:
                        if js[i] == "{":
                            depth += 1
                        elif js[i] == "}":
                            depth -= 1
                        i += 1
                    out.append("${" + _strip_js_comments_and_strings(js[start:i - 1]) + "}")
                    continue
                if quote != "`" and js[i] == "\n":
                    break                        # an unterminated string never swallows the file
                out.append(" " if js[i] != "\n" else "\n")
                i += 1
            out.append(quote)
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


#: (regex over code without comments/strings, level, ru, en)
_JS_SANDBOX_RULES: Tuple[Tuple[str, str, str, str], ...] = (
    (r"\b(?:window\.)?(?:localStorage|sessionStorage)\b", "error",
     "localStorage/sessionStorage в окне модуля бросают SecurityError (песочница без своего origin). Храните данные через Memu.save и поле persist.",
     "localStorage/sessionStorage throw SecurityError in the module window (a sandbox without an origin). Store data with Memu.save and the persist list."),
    (r"\bindexedDB\b|\bdocument\.cookie\b|\bcaches\.open\b", "error",
     "indexedDB, cookies и Cache API в песочнице модуля недоступны. Используйте Memu.save.",
     "indexedDB, cookies and the Cache API are unavailable in the module sandbox. Use Memu.save."),
    (r"(?<![\w.$])(?:window\.)?(?:alert|confirm|prompt)\s*\(", "error",
     "alert/confirm/prompt заблокированы (нет allow-modals): confirm() молча вернёт false. Нарисуйте свой диалог (<dialog> или div).",
     "alert/confirm/prompt are blocked (no allow-modals): confirm() silently returns false. Draw your own dialog (<dialog> or a div)."),
    (r"\bwindow\.open\s*\(|(?<![\w$])(?:window\.)?location\.(?:href\s*=|assign|replace)\b|(?<![\w.$])top\.location\b", "error",
     "Окно модуля не открывает вкладки и не уходит на другие адреса. Внешнюю ссылку открывает Memu.open({url}).",
     "The module window cannot open tabs or navigate away. Open an external link with Memu.open({url})."),
    (r"(?<![\w.$])eval\s*\(|\bnew\s+Function\s*\(|\bset(?:Timeout|Interval)\s*\(\s*[\"'`]", "error",
     "eval, new Function и строковый setTimeout запрещены политикой безопасности (нет 'unsafe-eval').",
     "eval, new Function and string setTimeout are forbidden by the security policy (no 'unsafe-eval')."),
    (r"(?<![\w.$])fetch\s*\(|\bXMLHttpRequest\b|\bnew\s+WebSocket\b|\bEventSource\b|\bnavigator\.sendBeacon\b", "error",
     "У модуля нет сети (connect-src 'none'): fetch, XMLHttpRequest и WebSocket не работают. Данные — через Memu.fetch, Memu.search, Memu.request.",
     "A module has no network (connect-src 'none'): fetch, XMLHttpRequest and WebSocket fail. Get data through Memu.fetch, Memu.search, Memu.request."),
    (r"\bnew\s+(?:Shared)?Worker\s*\(|\bserviceWorker\b", "error",
     "Web Worker и Service Worker в окне модуля не запускаются (политика без worker-src). Тяжёлую работу дробите через setTimeout/requestAnimationFrame.",
     "Web and Service Workers do not start in the module window (no worker-src). Split heavy work with setTimeout/requestAnimationFrame."),
    (r"\bnavigator\.clipboard\b", "warn",
     "navigator.clipboard в песочнице ненадёжен. Копируйте через Memu.copy(text), читайте через Memu.paste() (право clipboard).",
     "navigator.clipboard is unreliable in the sandbox. Copy with Memu.copy(text), read with Memu.paste() (clipboard permission)."),
    (r"\bgetUserMedia\b|\bmediaDevices\b", "error",
     "Микрофон странице модуля не дают. Записывайте через Memu.record.start()/stop() (право media).",
     "The module page gets no microphone. Record with Memu.record.start()/stop() (media permission)."),
    (r"^\s*import\s+[\w{*\"']|^\s*export\s+(?:default|const|function|class|let|var|\{)", "error",
     "main.js выполняется внутри функции, а не как ES-модуль: import/export — синтаксическая ошибка. Пишите весь код в одном файле.",
     "main.js runs inside a function, not as an ES module: import/export is a syntax error. Keep all code in one file."),
    (r"\bimport\s*\(", "error",
     "Динамический import() не загрузит файл: у модуля нет сети и других файлов.",
     "Dynamic import() cannot load anything: a module has no network and no other files."),
    (r"\bdocument\.write\s*\(", "warn",
     "document.write после загрузки стирает страницу вместе с мостом Memu. Меняйте DOM через innerHTML/textContent.",
     "document.write after load wipes the page and the Memu bridge. Change the DOM with innerHTML/textContent."),
    (r"\bMemu\.env\.default_model\b", "warn",
     "Поля Memu.env.default_model нет. Модель по умолчанию — Memu.manifest.bridge.model или первая текстовая из Memu.models().",
     "There is no Memu.env.default_model. Use Memu.manifest.bridge.model or the first text model from Memu.models()."),
    (r"(?<![\w.$])(?:window\.)?(?:parent|top)\.(?:document|location|window|frames|localStorage|S\b|Memu\w*|show\w*|open\w*|\$)", "error",
     "Окно MemuAI недоступно странице модуля (другой origin). Всё — через мост Memu.",
     "The MemuAI window is out of reach of the module page (another origin). Use the Memu bridge."),
)

#: Rules that look INSIDE string literals (the code above has its strings blanked): mistakes real models make
#: on the first try (Round 29 phase 2 live runs). Same tuple shape; scanned over main.js as written.
_JS_RAW_RULES: Tuple[Tuple[str, str, str, str], ...] = (
    (r"\b(?:fillStyle|strokeStyle|shadowColor)\s*=\s*[\"'`]\s*var\(\s*--", "error",
     "Canvas не понимает CSS-переменные: ctx.fillStyle = \"var(--accent)\" молча оставляет прежний цвет (чёрный). Берите цвет так: getComputedStyle(document.documentElement).getPropertyValue(\"--accent\").trim() — и перерисовывайте в Memu.onTheme.",
     "Canvas does not understand CSS variables: ctx.fillStyle = \"var(--accent)\" silently keeps the old colour (black). Read the colour with getComputedStyle(document.documentElement).getPropertyValue(\"--accent\").trim() and redraw in Memu.onTheme."),
    # The literal part of the key (up to the closing quote or a ${…}) against app/characters_api.py check_key:
    # a bad first character, a character outside [A-Za-z0-9._-/], an empty or «.»/«_»/«-»-led part, a part ending in «.».
    (r"\bMemu\.store\.(?:get|set|remove)\(\s*[\"'`](?:[\"'`]|[._/-]|[^\"'`$]*?(?:[^A-Za-z0-9._/\-\"'`$]|/[._/\-\"'`]|\.(?=[/\"'`])))"
     r"|\bMemu\.store\.list\(\s*[\"'`](?:[._/-]|[^\"'`$]*?(?:[^A-Za-z0-9._/\-\"'`$]|/[._/-]|\.(?=/)))", "error",
     "Ключ Memu.store — только латиница, цифры, «.», «_», «-» и до трёх частей через «/», каждая с буквы или цифры: \"days/2026-10-10\", а не \"pomodoro:2026-10-10\" (двоеточие, пробел, кириллица — «Недопустимый ключ хранилища»).",
     "A Memu.store key is Latin letters, digits, \".\", \"_\", \"-\" and up to three \"/\" parts, each starting with a letter or digit: \"days/2026-10-10\", not \"pomodoro:2026-10-10\" (a colon, a space or a non-Latin letter fails with “Invalid store key”)."),
    # e.key of the space bar is " ", never "Space" (that is e.code): a pause/jump on Space silently never fires.
    (r"\.key\s*[!=]==?\s*[\"'`](?:Space|Spacebar)[\"'`]"
     r"|^\s*[\"']?Space[\"']?\s*:(?=[\s\S]*\[\s*(?:e|ev|evt|event|k)\.key\s*\])"
     r"|\[\s*(?:e|ev|evt|event|k)\.key\s*\](?=[\s\S]*^\s*[\"']?Space[\"']?\s*:)", "warn",
     "У пробела e.key равен \" \" (строка с пробелом), а не \"Space\" — это e.code. Сравнение e.key с \"Space\" никогда не срабатывает: пауза/прыжок по пробелу не работает. Пишите e.code === \"Space\" или e.key === \" \".",
     "For the space bar e.key is \" \" (a space), not \"Space\" — that is e.code. Comparing e.key with \"Space\" never matches: pause/jump on Space does nothing. Use e.code === \"Space\" or e.key === \" \"."),
)

_HTML_RULES: Tuple[Tuple[str, str, str, str], ...] = (
    (r"<!doctype|<html[\s>]|<head[\s>]|<body[\s>]", "warn",
     "index.html — фрагмент, который вставляется внутрь <body>: <!DOCTYPE>, <html>, <head> и <body> не нужны.",
     "index.html is a fragment inserted into <body>: <!DOCTYPE>, <html>, <head> and <body> are not needed."),
    (r"<script\b", "error",
     "Скрипты в index.html выполнятся раньше моста Memu (или не загрузятся вовсе). Весь код — в main.js.",
     "Scripts in index.html run before the Memu bridge (or do not load at all). Put all code in main.js."),
    (r"<link\b[^>]*rel=[\"']?stylesheet|<link\b[^>]*href=", "error",
     "Внешние стили не загружаются. Все стили — в style.css.",
     "External stylesheets do not load. Put all styles in style.css."),
    (r"<iframe\b[^>]*\ssrc\s*=\s*[\"']?(?!about:)|<(?:object|embed)\b", "error",
     "iframe с адресом, object и embed не загрузятся (default-src 'none'). Вложенный предпросмотр — только iframe с srcdoc.",
     "An iframe with an address, object and embed never load (default-src 'none'). A nested preview works only as an iframe with srcdoc."),
    (r"<form\b[^>]*\baction\s*=", "warn",
     "Отправка формы запрещена (form-action 'none'). Обрабатывайте submit в main.js с event.preventDefault().",
     "Form submission is forbidden (form-action 'none'). Handle submit in main.js with event.preventDefault()."),
    (r"<a\b[^>]*\bdownload\b", "warn",
     "Ссылка download в окне модуля не скачивает. Сохраняйте файлы через Memu.download({name, text}).",
     "A download link does nothing in the module window. Save files with Memu.download({name, text})."),
    (r"<(?:img|video|audio|source)\b[^>]*\bsrc\s*=\s*[\"']?https?://", "error",
     "Картинки и медиа по внешнему адресу не грузятся (img-src data: blob:). Встраивайте data: URL или получайте их через мост.",
     "Images and media from an external address never load (img-src data: blob:). Embed data: URLs or get them through the bridge."),
    (r"\son[a-z]+\s*=\s*[\"'](?![^\"']*\bMemu\.)[^\"']*[A-Za-z_$][\w$]*\s*\(", "warn",
     "Атрибут onclick=\"имяФункции()\" не увидит функций из main.js: код модуля выполняется внутри функции. Вешайте обработчики в main.js (addEventListener) или кладите функцию в window.",
     "An onclick=\"myFunction()\" attribute cannot see functions from main.js: the module code runs inside a function. Attach handlers in main.js (addEventListener) or put the function on window."),
)

_CSS_RULES: Tuple[Tuple[str, str, str, str], ...] = (
    (r"@import\b", "error", "@import не загрузит файл: у модуля нет сети и других файлов.",
     "@import cannot load anything: a module has no network and no other files."),
    (r"url\(\s*[\"']?https?://", "error", "url(https://…) в стилях не загрузится. Используйте data: URL или градиенты.",
     "url(https://…) in styles never loads. Use data: URLs or gradients."),
    (r"@font-face[^}]*url\(\s*[\"']?(?!data:)", "error", "Шрифты подключаются только как data: URL; иначе возьмите var(--sans)/var(--mono).",
     "Fonts load only as data: URLs; otherwise use var(--sans)/var(--mono)."),
    (r"(?:^|[;{\s])(?:background|color)\s*:\s*#(?:fff|ffffff|000|000000)\b", "info",
     "Жёсткие #fff/#000 становятся нечитаемыми в другой теме. Берите токены var(--bg), var(--panel), var(--text).",
     "Hard-coded #fff/#000 become unreadable in the other theme. Use the var(--bg), var(--panel), var(--text) tokens."),
)


def _problem(level: str, field: str, ru: str, en: str, line: int = 0) -> Dict[str, Any]:
    row = {"level": level, "field": field, "ru": ru, "en": en}
    if line:
        row["line"] = line
    return row


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _scan(text: str, rules, field: str, flags=re.I | re.M) -> List[Dict[str, Any]]:
    found = []
    for pattern, level, ru, en in rules:
        match = re.search(pattern, text, flags)
        if match:
            found.append(_problem(level, field, ru, en, _line_of(text, match.start())))
    return found


def _top_level_await(js: str) -> int:
    """Line of an ``await`` outside any function, 0 when there is none (heuristic)."""
    depth_stack: List[bool] = []         # True when the brace opened a function body
    pending_function = False
    tokens = re.finditer(r"\bfunction\b|=>|\basync\b|[{}()]|\bawait\b", js)
    paren = 0
    for token in tokens:
        value = token.group(0)
        if value in ("function", "=>"):
            pending_function = True
        elif value == "(":
            paren += 1
        elif value == ")":
            paren = max(0, paren - 1)
        elif value == "{":
            depth_stack.append(pending_function)
            pending_function = False
        elif value == "}":
            if depth_stack:
                depth_stack.pop()
        elif value == "await":
            if not any(depth_stack) and not pending_function:
                return _line_of(js, token.start())
    return 0


def _node() -> str:
    return shutil.which("node") or ""


def js_syntax(js: str) -> Optional[str]:
    """The syntax error node reports for main.js wrapped like the host wraps it, None when fine
    or when Node.js is not installed."""
    node = _node()
    if not node or not js.strip():
        return None
    wrapped = "window.__memuModuleMain=function(){\n" + js + "\n};\n"
    with tempfile.TemporaryDirectory(prefix="memu-module-check-") as tmp:
        path = Path(tmp) / "main.js"
        path.write_text(wrapped, encoding="utf-8")
        try:
            result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return None
    if result.returncode == 0:
        return None
    message = (result.stderr or result.stdout or "").strip()
    match = re.search(r"main\.js:(\d+)", message)
    line = int(match.group(1)) - 1 if match else 0
    error = next((row for row in message.splitlines() if "Error" in row), message.splitlines()[-1] if message else "")
    return f"line {line}: {error}" if line else error


def validate_package(source: Dict[str, Any], *, check_syntax: bool = True,
                     existing_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """Every check on a loaded package; ``source`` comes from :func:`load_source` / :func:`from_payload`."""
    problems: List[Dict[str, Any]] = []
    manifest = dict(source.get("manifest") or {})
    files = dict(source.get("files") or {})
    html, css, js = (str(files.get("index.html") or ""), str(files.get("style.css") or ""), str(files.get("main.js") or ""))
    i18n_raw = files.get("i18n.json") or ""

    # identity -----------------------------------------------------------
    module_id = str(manifest.get("id") or "").strip()
    if not module_id:
        problems.append(_problem("error", "manifest", "В manifest.json нет id.", "manifest.json has no id."))
    else:
        try:
            host.validate_id(module_id)
        except ValueError as exc:
            problems.append(_problem("error", "manifest", str(exc),
                                     "id: lowercase latin letters, digits and hyphens, up to 41 characters, starting with a letter"
                                     " (and not a Windows device name like con or nul)."))
        if module_id in host.BUILTIN_ORDER:
            problems.append(_problem("error", "manifest", f"id «{module_id}» занят встроенным модулем.",
                                     f"The id “{module_id}” belongs to a built-in module."))
        elif existing_ids and module_id in existing_ids:
            problems.append(_problem("warn", "manifest", f"Модуль «{module_id}» уже установлен: при импорте укажите новый id.",
                                     f"A module “{module_id}” is already installed: give a new id when importing."))
    if source.get("format") and source.get("format") != host.PACKAGE_FORMAT:
        problems.append(_problem("warn", "package", f"Поле format должно быть «{host.PACKAGE_FORMAT}».",
                                 f"The format field should be “{host.PACKAGE_FORMAT}”."))

    # manifest shape -----------------------------------------------------
    unknown = sorted(key for key in manifest if key not in MANIFEST_KEYS)
    if unknown:
        problems.append(_problem("info", "manifest", "Хост не читает эти поля manifest.json: " + ", ".join(unknown),
                                 "The host ignores these manifest.json fields: " + ", ".join(unknown)))
    for key, allowed in (("window", WINDOW_KEYS), ("bridge", BRIDGE_KEYS), ("launcher", LAUNCHER_KEYS)):
        value = manifest.get(key)
        if value is not None and not isinstance(value, dict):
            problems.append(_problem("error", "manifest", f"{key} должен быть объектом.", f"{key} must be an object."))
        elif isinstance(value, dict):
            extra = sorted(name for name in value if name not in allowed)
            if extra:
                problems.append(_problem("info", "manifest", f"Неизвестные поля {key}: " + ", ".join(extra),
                                         f"Unknown {key} fields: " + ", ".join(extra)))
    launcher = manifest.get("launcher") if isinstance(manifest.get("launcher"), dict) else {}
    if launcher.get("slot") and launcher.get("slot") not in host.LAUNCHER_SLOTS:
        problems.append(_problem("warn", "manifest", "launcher.slot: " + ", ".join(host.LAUNCHER_SLOTS) + " — иначе sidebar.",
                                 "launcher.slot: " + ", ".join(host.LAUNCHER_SLOTS) + " — otherwise sidebar."))
    for key, limit in (("title", 80), ("title_en", 80), ("summary", 400), ("summary_en", 400), ("version", 24), ("author", 80)):
        if len(str(manifest.get(key) or "")) > limit:
            problems.append(_problem("warn", "manifest", f"{key} длиннее {limit} символов — обрежется.",
                                     f"{key} is longer than {limit} characters — it will be cut."))
    if not manifest.get("title"):
        problems.append(_problem("warn", "manifest", "Нет title: на кнопке будет виден id.", "No title: the button shows the id."))
    if manifest.get("title") and not manifest.get("title_en"):
        problems.append(_problem("info", "manifest", "Нет title_en: в английском интерфейсе останется title.",
                                 "No title_en: the English UI keeps title."))
    # Round 38: contributes — panels, commands, skill, widgets (app/module_integration.py)
    if manifest.get("contributes") is not None:
        from .module_integration import validate_contributes
        for message in validate_contributes(manifest.get("contributes"), builtin=module_id in host.BUILTIN_ORDER):
            problems.append(_problem("warn", "manifest", "contributes: " + message, "contributes: " + message))
        contributes = manifest["contributes"] if isinstance(manifest.get("contributes"), dict) else {}
        for item in contributes.get("panels") or []:
            names = [item.get("entry"), *(item.get("scripts") or []), *(item.get("styles") or [])] if isinstance(item, dict) else []
            for name in names:
                if name and files and name not in files:
                    problems.append(_problem("warn", "manifest", f"contributes: файла {name} нет в пакете.",
                                             f"contributes: the file {name} is not in the package."))
    accent = str(manifest.get("accent") or "")
    if accent and not re.fullmatch(r"#[0-9a-fA-F]{3,8}", accent):
        problems.append(_problem("warn", "manifest", "accent — цвет вида #rrggbb, иначе он не применится.",
                                 "accent must be a #rrggbb colour, otherwise it is ignored."))
    # Round 39: icon — a glyph name, a package file, inline SVG or a data URL; icon_bg (app/module_icons.py)
    from .module_icons import icon_kind, validate as validate_icon
    listed = files if files and any(name not in PACKAGE_FILES for name in files) else None  # a full file listing
    for level, ru, en in validate_icon(manifest, listed):
        problems.append(_problem(level, "manifest", ru, en))
    icon = str(manifest.get("icon") or "") if isinstance(manifest.get("icon"), str) else ""
    if icon and icon_kind(icon) == "glyph" and icon not in ICON_NAMES:
        problems.append(_problem("info", "manifest", f"Иконки «{icon}» нет в наборе MemuAI — будет значок по умолчанию.",
                                 f"There is no “{icon}” icon in the MemuAI set — the default glyph is used."))
    window = manifest.get("window") if isinstance(manifest.get("window"), dict) else {}
    for key, low, high in (("default_width", 240, 2400), ("default_height", 200, 1600)):
        try:
            value = window.get(key)
            if value is not None and not (low <= int(value) <= high):
                problems.append(_problem("info", "manifest", f"window.{key} вне {low}–{high} — хост поправит.",
                                         f"window.{key} is outside {low}–{high} — the host clamps it."))
        except (TypeError, ValueError):
            problems.append(_problem("warn", "manifest", f"window.{key} должен быть числом.", f"window.{key} must be a number."))
    instructions = str((manifest.get("bridge") or {}).get("instructions") or "") if isinstance(manifest.get("bridge"), dict) else ""
    if len(instructions) > 20_000:
        problems.append(_problem("warn", "manifest", "bridge.instructions длиннее 20 000 символов — обрежется.",
                                 "bridge.instructions is longer than 20 000 characters — it will be cut."))
    normalized: Dict[str, Any] = {}
    try:
        normalized = host.normalize_manifest(manifest, module_id=module_id or "module")
    except ValueError as exc:
        problems.append(_problem("error", "manifest", str(exc), "permissions must be a list"))

    # permissions --------------------------------------------------------
    permissions = manifest.get("permissions")
    if isinstance(permissions, list):
        bad = [str(name) for name in permissions if name not in host.PERMISSIONS]
        if bad:
            problems.append(_problem("warn", "manifest", "Неизвестные права (будут отброшены): " + ", ".join(bad),
                                     "Unknown permissions (dropped): " + ", ".join(bad)))
    elif permissions is None:
        problems.append(_problem("warn", "manifest", "Нет списка permissions — модуль получит ВСЕ права (кроме net-import). Перечислите только нужные.",
                                 "No permissions list — the module gets EVERY permission (except net-import). List only the ones it needs."))
    # net_import (Round 29): sources and the allow list of direct-link hosts ------------------------
    block = manifest.get("net_import")
    if block is not None:
        from .character_import import SOURCES as _SOURCES, clean_host as _clean_host
        if not isinstance(block, dict):
            problems.append(_problem("warn", "manifest", "net_import должен быть объектом {sources, hosts} — поле отброшено.",
                                     "net_import must be an object {sources, hosts} — the field is dropped."))
        else:
            bad_sources = [str(x) for x in (block.get("sources") or []) if str(x) not in _SOURCES] \
                if isinstance(block.get("sources"), list) else []
            if bad_sources:
                problems.append(_problem("warn", "manifest", "Неизвестные источники net_import.sources (будут отброшены): " + ", ".join(bad_sources)
                                         + ". Есть: " + ", ".join(_SOURCES),
                                         "Unknown net_import.sources (dropped): " + ", ".join(bad_sources) + ". Known: " + ", ".join(_SOURCES)))
            bad_hosts = [str(x) for x in (block.get("hosts") or []) if not _clean_host(x)] \
                if isinstance(block.get("hosts"), list) else []
            if bad_hosts:
                problems.append(_problem("warn", "manifest", "net_import.hosts: недопустимые хосты (IP, localhost, LAN — запрещены): " + ", ".join(bad_hosts),
                                         "net_import.hosts: invalid hosts (IP, localhost, LAN are refused): " + ", ".join(bad_hosts)))
        if not (isinstance(permissions, list) and "net-import" in permissions):
            problems.append(_problem("info", "manifest", "net_import задан, но права «net-import» нет в permissions — Memu.fetchImport не сработает.",
                                     "net_import is set but “net-import” is not in permissions — Memu.fetchImport will not work."))
    granted = set(normalized.get("permissions") or []) if normalized else set()
    code = _strip_js_comments_and_strings(js)
    needs = method_permissions()
    used_methods = sorted(set(re.findall(r"\bMemu(?:AI)?\.(record\.\w+|store\.\w+|toolbar\.\w+|\w+)\s*\(", code)) |
                          set("call:" + m for m in re.findall(r"\bMemu\.call\(\s*[\"'](\w+)", js)))
    missing: Dict[str, List[str]] = {}
    for method in used_methods:
        if method.startswith("call:"):
            action = method[5:]
            permission = host.ACTION_PERMISSION.get(action, "")
            method = f'call("{action}")'
        elif method.startswith("i18n.") or method in ("on", "onTheme", "onLanguage", "t", "ready"):
            continue
        else:
            if method not in needs:
                problems.append(_problem("error", "js", f"У моста нет метода Memu.{method}().",
                                         f"The bridge has no Memu.{method}() method."))
                continue
            permission = needs.get(method, "")
        if permission and permission not in granted:
            missing.setdefault(permission, []).append("Memu." + method if not method.startswith("call") else "Memu." + method)
    for permission, methods in sorted(missing.items()):
        problems.append(_problem("error", "manifest", f"Нужно право «{permission}» для " + ", ".join(sorted(set(methods))) +
                                 ": добавьте его в permissions.",
                                 f"The “{permission}” permission is required by " + ", ".join(sorted(set(methods))) +
                                 ": add it to permissions."))
    if isinstance(permissions, list):
        used_permissions = {needs.get(m, "") for m in used_methods if not m.startswith("call:")}
        used_permissions |= {host.ACTION_PERMISSION.get(m[5:], "") for m in used_methods if m.startswith("call:")}
        unused = sorted(p for p in granted if p not in used_permissions)
        if unused:
            problems.append(_problem("info", "manifest", "Права не используются кодом (лишние права — лишний риск): " + ", ".join(unused),
                                     "Permissions the code never uses (extra rights, extra risk): " + ", ".join(unused)))

    # the host's own checks (persist vs Memu.save, i18n keys, </script>…) --
    try:
        i18n_value: Any = json.loads(i18n_raw) if isinstance(i18n_raw, str) and i18n_raw.strip() else (i18n_raw or {})
    except json.JSONDecodeError as exc:
        i18n_value = {}
        problems.append(_problem("error", "i18n", f"i18n.json: неверный JSON в строке {exc.lineno}, столбце {exc.colno}",
                                 f"i18n.json: invalid JSON at line {exc.lineno}, column {exc.colno}"))
    host_report = host.validate_module_source({"html": html, "css": css, "js": js, "i18n": i18n_value,
                                               "persist": normalized.get("persist") if normalized else manifest.get("persist") or []})
    for row in host_report["problems"]:
        if row["en"].startswith("External script and link"):
            continue                       # the stricter html rules below say it
        problems.append(dict(row, level="error" if row["field"] == "persist" else row["level"]))
    if isinstance(i18n_value, dict) and i18n_value:
        locales = set(i18n_value)
        if not {"ru", "en"} <= locales:
            problems.append(_problem("warn", "i18n", "Нужны оба словаря MemuAI: «ru» и «en».",
                                     "Both MemuAI dictionaries are needed: “ru” and “en”."))
    elif re.search(r"[А-Яа-яЁё]", html + js) and re.search(r"[A-Za-z]{4,}", html):
        problems.append(_problem("warn", "i18n", "Тексты зашиты в разметку и код без i18n.json: модуль не переключит язык вместе с MemuAI.",
                                 "Texts are hard-coded without i18n.json: the module will not follow the MemuAI language."))

    # sizes ----------------------------------------------------------------
    for name, text in (("index.html", html), ("style.css", css), ("main.js", js)):
        if len(text) > host._CODE_LIMIT:
            problems.append(_problem("error", name, f"{name} длиннее {host._CODE_LIMIT} символов.",
                                     f"{name} is longer than {host._CODE_LIMIT} characters."))
    if not (html.strip() or js.strip()):
        problems.append(_problem("error", "package", "В пакете нет кода: index.html и main.js пустые.",
                                 "The package has no code: index.html and main.js are empty."))

    # sandbox ----------------------------------------------------------------
    problems.extend(_scan(code, _JS_SANDBOX_RULES, "js"))
    problems.extend(_scan(js, _JS_RAW_RULES, "js"))
    problems.extend(_scan(html, _HTML_RULES, "html"))
    problems.extend(_scan(css, _CSS_RULES, "css"))
    await_line = _top_level_await(code)
    if await_line:
        problems.append(_problem("error", "js", "await вне функции: main.js оборачивается в обычную функцию. Используйте (async () => { … })();",
                                 "await outside a function: main.js is wrapped in a plain function. Use (async () => { … })();", await_line))
    if re.search(r"\bdocument\.querySelector\(\s*[\"'`]#?(?:app|root)[\"'`]\)", js) and not re.search(r"id=[\"'](?:app|root)[\"']", html):
        problems.append(_problem("warn", "js", "main.js ищет #app/#root, которого нет в index.html.",
                                 "main.js looks for #app/#root, which index.html does not have."))
    ids_in_js = set(re.findall(r"querySelector\(\s*[\"'`]#([A-Za-z][\w-]*)[\"'`]\s*\)|getElementById\(\s*[\"'`]([A-Za-z][\w-]*)[\"'`]\s*\)", js))
    ids_in_html = set(re.findall(r"\bid\s*=\s*[\"']([^\"']+)[\"']", html))
    ids_made_in_js = set(re.findall(r"\bid\s*=\s*\\?[\"']([A-Za-z][\w-]*)", js)) | set(re.findall(r"\.id\s*=\s*[\"'`]([A-Za-z][\w-]*)", js))
    lost = sorted({a or b for a, b in ids_in_js} - ids_in_html - ids_made_in_js)
    if lost:
        problems.append(_problem("warn", "js", "Код ищет элементы, которых нет в index.html: #" + ", #".join(lost[:10]),
                                 "The code looks for elements index.html does not have: #" + ", #".join(lost[:10])))
    # Round 30: one header. A module that paints its own top bar gets two rows of buttons under the window's row.
    if re.search(r"<header\b", html, re.I) and not re.search(r"\bMemu(?:AI)?\.toolbar\b", js):
        problems.append(_problem("info", "html", "Модуль рисует свою верхнюю панель (<header>). В окне MemuAI имя модуля и кнопки окна уже есть "
                                 "в строке заголовка: передайте свои действия в Memu.toolbar.set([...]) (или Memu.toolbar.adopt({...}) для "
                                 "готовой панели), иначе над модулем будет два ряда кнопок.",
                                 "The module paints its own top bar (<header>). The MemuAI window already has the module name and window "
                                 "buttons in its title row: hand your actions to Memu.toolbar.set([...]) (or Memu.toolbar.adopt({...}) for "
                                 "an existing bar), otherwise there are two rows of buttons above the module."))
    if check_syntax:
        error = js_syntax(js)
        if error:
            problems.append(_problem("error", "js", "Синтаксическая ошибка в main.js: " + error, "Syntax error in main.js: " + error))
    if source.get("extra_files"):
        problems.append(_problem("info", "package", "Эти файлы не войдут в модуль (берутся только 5 файлов пакета): " +
                                 ", ".join(source["extra_files"][:12]),
                                 "These files are not part of the module (only the 5 package files are read): " +
                                 ", ".join(source["extra_files"][:12])))

    # dedupe ---------------------------------------------------------------
    seen = set()
    unique = []
    for row in problems:
        key = (row["level"], row["field"], row["en"])
        if key not in seen:
            seen.add(key)
            unique.append(row)
    order = {"error": 0, "warn": 1, "info": 2}
    unique.sort(key=lambda row: order.get(row["level"], 3))
    counts = {level: sum(1 for row in unique if row["level"] == level) for level in ("error", "warn", "info")}
    try:
        i18n_clean = host.normalize_i18n(i18n_value) if isinstance(i18n_value, (dict, str)) else {}
    except ValueError:
        i18n_clean = {}
    package = {
        "format": host.PACKAGE_FORMAT, "version": host.PACKAGE_VERSION,
        "manifest": {key: value for key, value in manifest.items() if key not in ("html", "css", "js", "i18n", "enabled")},
        "files": {"index.html": html, "style.css": css, "main.js": js, "i18n.json": i18n_clean},
    }
    return {
        "ok": counts["error"] == 0, "counts": counts, "problems": unique,
        "id": module_id, "title": str(manifest.get("title") or ""), "title_en": str(manifest.get("title_en") or ""),
        "permissions": sorted(granted), "persist": list(normalized.get("persist") or []) if normalized else [],
        "stats": {"html": len(html), "css": len(css), "js": len(js), "locales": sorted((i18n_clean or {}).keys())},
        "origin": source.get("origin", ""), "package": package,
    }


def validate_path(path: Any, **options: Any) -> Dict[str, Any]:
    return validate_package(load_source(path), **options)


def validate_zip_base64(data: str, name: str = "module.zip", **options: Any) -> Dict[str, Any]:
    payload = str(data or "")
    if payload.startswith("data:"):
        payload = payload.split(",", 1)[-1]
    try:
        raw = base64.b64decode(payload, validate=False)
    except (ValueError, TypeError) as exc:
        raise ValueError("the archive is not valid base64") from exc
    return validate_package(from_zip_bytes(raw, name), **options)


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def format_report(report: Dict[str, Any], lang: str = "ru") -> str:
    en = lang == "en"
    marks = {"error": "✖", "warn": "▲", "info": "·"}
    names = {"error": "ошибки" if not en else "errors", "warn": "предупреждения" if not en else "warnings",
             "info": "подсказки" if not en else "hints"}
    title = report.get("title_en" if en else "title") or report.get("title") or ""
    lines = [("Module" if en else "Модуль") + f" «{report.get('id') or '?'}»" + (f" — {title}" if title else ""),
             ("Source: " if en else "Источник: ") + str(report.get("origin") or "")]
    counts = report.get("counts") or {}
    lines.append(", ".join(f"{names[level]}: {counts.get(level, 0)}" for level in ("error", "warn", "info")))
    lines.append("")
    for row in report.get("problems") or []:
        where = row.get("field", "")
        if row.get("line"):
            where += f":{row['line']}"
        lines.append(f"{marks.get(row['level'], '-')} [{where}] {row['en' if en else 'ru']}")
    if not report.get("problems"):
        lines.append("Everything looks right." if en else "Всё в порядке.")
    lines.append("")
    lines.append(("RESULT: ready to import" if en else "ИТОГ: можно импортировать") if report.get("ok")
                 else ("RESULT: fix the errors above" if en else "ИТОГ: исправьте ошибки выше"))
    return "\n".join(lines)


ICON_NAMES = ("text", "chat", "sparkle", "zap", "image", "work", "bot", "settings", "palette", "search", "globe", "list",
              "clock", "bell", "keyboard", "user", "users", "monitor", "terminal", "target", "mic", "headphones",
              "volume", "download", "folder", "info", "play", "undo", "redo", "scissors", "clipboard", "branch", "pin",
              "edit", "trash", "copy", "retry", "attach", "send", "stop", "temp", "focus", "brain", "export", "check",
              "unread", "moon", "sun", "menu", "back", "forward", "sidebar", "plus", "more", "close", "book", "link",
              "camera", "chart", "research", "wave", "minus", "external", "fit", "updown")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.modules_validate",
                                     description="Check a MemuAI module (folder, .zip or .memu-module.json) before importing it.")
    parser.add_argument("path")
    parser.add_argument("--lang", choices=("ru", "en"), default="ru")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--export", metavar="FILE", help="write the package as an importable .memu-module.json")
    parser.add_argument("--no-syntax", action="store_true", help="skip the Node.js syntax check")
    args = parser.parse_args(argv)
    try:
        report = validate_path(args.path, check_syntax=not args.no_syntax)
    except ValueError as exc:
        print(("Cannot read the module: " if args.lang == "en" else "Не удалось прочитать модуль: ") + str(exc), file=sys.stderr)
        return 2
    if args.export:
        Path(args.export).write_text(json.dumps(report["package"], ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps({key: value for key, value in report.items() if key != "package"}, ensure_ascii=False, indent=2))
    else:
        print(format_report(report, args.lang))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
