"""Round 38: modules integrate with MemuAI — panels, commands (skills for the AI) and widgets.

A module declares ``contributes`` in its manifest::

    "contributes": {
      "panels":   [{"id", "title", "title_en", "summary", "summary_en", "icon", "size": "s|m|l|w",
                    "entry": "integration/x.html", "scripts": ["integration/x.js"], "styles": ["integration/x.css"],
                    "height": 120..640, "settings": [{key, type: bool|choice|text, label, label_en, default, choices}]}],
      "commands": [{"id", "title", "title_en", "description", "description_en", "args": {JSON schema object},
                    "phrases": ["включи трек {query}"], "phrases_en": ["play {query}"], "danger": false,
                    "run": {"handler": "music.play"} | {"open": true}}],
      "skill":    "integration/skill.md" | {"text": "...", "text_en": "..."},
      "widgets":  [{"id", "slot": "quick.ring|quick.card|chat.composer|main.dock|chat.cards", "kind": "equalizer|nowplaying|miniplayer|dock|cards",
                    "source": "music.player", "title", "title_en", "settings": [...]}]
    }

Everything here works only for ENABLED modules. Panels run in the same sandbox iframe as the module window
(``modules_host.module_document``: CSP without network, bridge ``Memu.*`` limited by the module's permissions).
Commands run on the server: built-in modules have Python handlers (:data:`HANDLERS`, ``run.handler``), any module
can ask MemuAI to open it with the command (``run.open``; the module receives ``Memu.on("command")``).
Widgets are host presets (the host draws them, the module chooses kind, slot and settings) — no module code runs in
the chat or Quick Access windows. Widget / panel settings are stored in ``<modules root>/integration.json``.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from . import modules_host

PANEL_SIZES = ("s", "m", "l", "w")
# Round 39 (r39-integ): main.dock — the host player of the MemuAI window (music-dock.js), chat.cards — command result cards in the chat
WIDGET_SLOTS = ("quick.ring", "quick.card", "chat.composer", "main.dock", "chat.cards")
WIDGET_KINDS = ("equalizer", "nowplaying", "miniplayer", "dock", "cards")
WIDGET_SOURCES = ("music.player",)
SETTING_TYPES = ("bool", "choice", "text", "color")
LIMITS = {"panels": 8, "commands": 24, "widgets": 6, "settings": 12, "phrases": 8}
_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")
_FILE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]{0,120}\.(?:html|js|css|md)$")
_LOCK = threading.RLock()


def _text(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _file(value: Any) -> str:
    name = str(value or "").strip().replace("\\", "/")
    if not _FILE_RE.match(name) or ".." in name.split("/"):
        return ""
    return name


def _settings(raw: Any, errors: List[str], where: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in (raw if isinstance(raw, list) else [])[:LIMITS["settings"]]:
        if not isinstance(item, dict) or not _ID_RE.match(str(item.get("key") or "")):
            errors.append(f"{where}: настройка без ключа / a setting without a key")
            continue
        kind = str(item.get("type") or "bool")
        if kind not in SETTING_TYPES:
            errors.append(f"{where}.{item['key']}: тип {kind!r} неизвестен / unknown type")
            continue
        row = {"key": str(item["key"]), "type": kind, "label": _text(item.get("label") or item["key"], 80),
               "label_en": _text(item.get("label_en") or item.get("label") or item["key"], 80)}
        if kind == "choice":
            choices = []
            for choice in (item.get("choices") or [])[:12]:
                if isinstance(choice, dict) and _text(choice.get("value"), 40):
                    choices.append({"value": _text(choice["value"], 40), "label": _text(choice.get("label") or choice["value"], 60),
                                    "label_en": _text(choice.get("label_en") or choice.get("label") or choice["value"], 60)})
            if not choices:
                errors.append(f"{where}.{item['key']}: у выбора нет вариантов / a choice without choices")
                continue
            row["choices"] = choices
            default = str(item.get("default") or choices[0]["value"])
            row["default"] = default if default in [c["value"] for c in choices] else choices[0]["value"]
        elif kind == "bool":
            row["default"] = item.get("default", True) is not False
        else:
            row["default"] = _text(item.get("default"), 200)
        out.append(row)
    return out


def _schema(raw: Any, errors: List[str], where: str) -> Dict[str, Any]:
    """A small JSON schema of command arguments: an object of string/number/boolean/enum properties."""
    if raw in (None, {}):
        return {"type": "object", "properties": {}, "required": []}
    if not isinstance(raw, dict) or not isinstance(raw.get("properties", {}), dict):
        errors.append(f"{where}: args должен быть JSON-схемой объекта / args must be an object schema")
        return {"type": "object", "properties": {}, "required": []}
    props: Dict[str, Any] = {}
    for key, spec in list(raw.get("properties", {}).items())[:8]:
        if not _ID_RE.match(str(key)) or not isinstance(spec, dict):
            errors.append(f"{where}: аргумент {key!r} некорректен / bad argument")
            continue
        kind = spec.get("type") if spec.get("type") in ("string", "number", "integer", "boolean") else "string"
        row = {"type": kind, "description": _text(spec.get("description"), 200)}
        if isinstance(spec.get("enum"), list):
            row["enum"] = [str(v)[:60] for v in spec["enum"][:16]]
        props[str(key)] = row
    required = [str(k) for k in (raw.get("required") or []) if str(k) in props]
    return {"type": "object", "properties": props, "required": required}


def normalize_contributes(raw: Any, *, builtin: bool = False, errors: Optional[List[str]] = None) -> Dict[str, Any]:
    """Clean ``manifest.contributes``. Problems are appended to ``errors`` (the item is dropped)."""
    errors = errors if errors is not None else []
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        errors.append("contributes должен быть объектом / contributes must be an object")
        return {}
    out: Dict[str, Any] = {}
    panels, seen = [], set()
    for item in (raw.get("panels") if isinstance(raw.get("panels"), list) else [])[:LIMITS["panels"]]:
        pid = str((item or {}).get("id") or "") if isinstance(item, dict) else ""
        if not _ID_RE.match(pid) or pid in seen:
            errors.append(f"panels: id {pid!r} некорректен или повторяется / bad or duplicate id")
            continue
        entry = _file(item.get("entry"))
        if not entry.endswith(".html"):
            errors.append(f"panels.{pid}: entry — HTML-файл модуля / entry must be an HTML file of the module")
            continue
        data = _text(item.get("data"), 60)
        if data and not builtin:
            errors.append(f"panels.{pid}: data доступен только встроенным модулям / data is for built-in modules only")
            data = ""
        seen.add(pid)
        panels.append({
            "id": pid, "title": _text(item.get("title") or pid, 60), "title_en": _text(item.get("title_en") or item.get("title") or pid, 60),
            "summary": _text(item.get("summary"), 160), "summary_en": _text(item.get("summary_en") or item.get("summary"), 160),
            "icon": _text(item.get("icon") or "", 24), "entry": entry,
            "scripts": [f for f in (_file(s) for s in (item.get("scripts") or [])[:6]) if f.endswith(".js")],
            "styles": [f for f in (_file(s) for s in (item.get("styles") or [])[:4]) if f.endswith(".css")],
            "size": item.get("size") if item.get("size") in PANEL_SIZES else "m",
            "height": max(96, min(640, int(item.get("height") or 220))) if str(item.get("height") or "220").isdigit() else 220,
            "settings": _settings(item.get("settings"), errors, f"panels.{pid}"),
            **({"data": data} if data else {}),
        })
    if panels:
        out["panels"] = panels
    commands, seen = [], set()
    for item in (raw.get("commands") if isinstance(raw.get("commands"), list) else [])[:LIMITS["commands"]]:
        cid = str((item or {}).get("id") or "") if isinstance(item, dict) else ""
        if not _ID_RE.match(cid) or cid in seen:
            errors.append(f"commands: id {cid!r} некорректен или повторяется / bad or duplicate id")
            continue
        run = item.get("run") if isinstance(item.get("run"), dict) else {}
        handler = _text(run.get("handler"), 60)
        if handler and not builtin:
            errors.append(f"commands.{cid}: run.handler доступен только встроенным модулям — используйте run.open / "
                          "run.handler is for built-in modules only — use run.open")
            handler = ""
        if not handler and not run.get("open"):
            errors.append(f"commands.{cid}: нужен run.open (или run.handler у встроенного) / run.open is required")
            continue
        seen.add(cid)
        commands.append({
            "id": cid, "title": _text(item.get("title") or cid, 60), "title_en": _text(item.get("title_en") or item.get("title") or cid, 60),
            "description": _text(item.get("description"), 300),
            "description_en": _text(item.get("description_en") or item.get("description"), 300),
            "args": _schema(item.get("args"), errors, f"commands.{cid}"),
            "phrases": [_text(p, 80) for p in (item.get("phrases") or [])[:LIMITS["phrases"]] if _text(p, 80)],
            "phrases_en": [_text(p, 80) for p in (item.get("phrases_en") or [])[:LIMITS["phrases"]] if _text(p, 80)],
            "danger": bool(item.get("danger")),
            "run": {"handler": handler} if handler else {"open": True},
        })
    if commands:
        out["commands"] = commands
    skill = raw.get("skill")
    if isinstance(skill, str) and _file(skill).endswith(".md"):
        out["skill"] = {"file": _file(skill)}
    elif isinstance(skill, dict) and (skill.get("text") or skill.get("text_en")):
        out["skill"] = {"text": str(skill.get("text") or "")[:6000], "text_en": str(skill.get("text_en") or skill.get("text") or "")[:6000]}
    elif skill:
        errors.append("skill: путь к .md-файлу модуля или {text, text_en} / a .md file of the module or {text, text_en}")
    widgets, seen = [], set()
    for item in (raw.get("widgets") if isinstance(raw.get("widgets"), list) else [])[:LIMITS["widgets"]]:
        wid = str((item or {}).get("id") or "") if isinstance(item, dict) else ""
        if not _ID_RE.match(wid) or wid in seen:
            errors.append(f"widgets: id {wid!r} некорректен или повторяется / bad or duplicate id")
            continue
        if item.get("slot") not in WIDGET_SLOTS or item.get("kind") not in WIDGET_KINDS or item.get("source") not in WIDGET_SOURCES:
            errors.append(f"widgets.{wid}: slot {WIDGET_SLOTS}, kind {WIDGET_KINDS}, source {WIDGET_SOURCES}")
            continue
        seen.add(wid)
        widgets.append({"id": wid, "slot": item["slot"], "kind": item["kind"], "source": item["source"],
                        "title": _text(item.get("title") or wid, 60), "title_en": _text(item.get("title_en") or item.get("title") or wid, 60),
                        "settings": _settings(item.get("settings"), errors, f"widgets.{wid}")})
    if widgets:
        out["widgets"] = widgets
    return out


def validate_contributes(raw: Any, *, builtin: bool = False, folder: Optional[Path] = None) -> List[str]:
    """Errors of a ``contributes`` block (and missing files when the package folder is known)."""
    errors: List[str] = []
    clean = normalize_contributes(raw, builtin=builtin, errors=errors)
    if folder is not None:
        names = [p["entry"] for p in clean.get("panels", [])] + [f for p in clean.get("panels", []) for f in p["scripts"] + p["styles"]]
        names += [clean["skill"]["file"]] if clean.get("skill", {}).get("file") else []
        for name in names:
            if not (Path(folder) / name).is_file():
                errors.append(f"файл {name} не найден в пакете / file {name} is missing from the package")
    for panel in clean.get("panels", []):
        if panel.get("data") and panel["data"] not in DATA:
            errors.append(f"panels.{panel['id']}: источник данных {panel['data']!r} неизвестен / unknown data source")
    for cmd in clean.get("commands", []):
        handler = cmd["run"].get("handler")
        if handler and handler not in HANDLERS:
            errors.append(f"commands.{cmd['id']}: обработчик {handler!r} неизвестен / unknown handler")
    return errors


# ------------------------------------------------------------------------------- package files (export/import)

PACKAGE_FILE_LIMIT = 400_000
PACKAGE_FILES_TOTAL = 2_000_000


def contributed_files(raw: Any) -> List[str]:
    """Files of the package a ``contributes`` block names: panel pages / scripts / styles and the skill."""
    clean = normalize_contributes(raw, builtin=False)
    names = [f for p in clean.get("panels", []) for f in [p["entry"], *p["scripts"], *p["styles"]]]
    names += [clean["skill"]["file"]] if clean.get("skill", {}).get("file") else []
    return list(dict.fromkeys(names))


def package_files(folder: Path, raw: Any) -> Dict[str, str]:
    """``{name: text}`` of the contributed files for an exported package (missing files are skipped)."""
    out: Dict[str, str] = {}
    for name in contributed_files(raw):
        path = Path(folder) / name
        if path.is_file():
            try:
                out[name] = path.read_text(encoding="utf-8")[:PACKAGE_FILE_LIMIT]
            except (OSError, UnicodeDecodeError):
                continue
    return out


def write_package_files(folder: Path, raw: Any, files: Dict[str, Any]) -> List[str]:
    """Write the contributed files an imported package carries into its folder (only names ``contributes`` declares,
    no ``..``, text only, size-limited). Returns the written names."""
    written, total = [], 0
    for name in contributed_files(raw):
        text = files.get(name) if isinstance(files, dict) else None
        if not isinstance(text, str) or len(text) > PACKAGE_FILE_LIMIT or total + len(text) > PACKAGE_FILES_TOTAL:
            continue
        target = (Path(folder) / name).resolve()
        if Path(folder).resolve() not in target.parents:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        total += len(text)
        written.append(name)
    return written


# ---------------------------------------------------------------------------------------------------- catalogue

def _root(root: Optional[Path] = None) -> Path:
    return Path(root) if root is not None else modules_host.default_root()


def _package_files(root: Path, module_id: str) -> Optional[Path]:
    return modules_host._package_dir(root, module_id)


def contributions(root: Optional[Path] = None, *, include_disabled: bool = False) -> List[Dict[str, Any]]:
    """``[{module, title, title_en, icon, accent, enabled, panels, commands, widgets, has_skill}]`` of modules with a
    ``contributes`` block (enabled ones only unless ``include_disabled``)."""
    root = _root(root)
    out = []
    from .safe_mode import is_safe_mode
    if is_safe_mode():
        return out
    for row in modules_host.list_modules(root):
        if not row.get("enabled") and not include_disabled:
            continue
        folder = _package_files(root, row["id"])
        if folder is None:
            continue
        try:
            manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        clean = normalize_contributes(manifest.get("contributes"), builtin=bool(row.get("builtin")))
        if not clean:
            continue
        out.append({"module": row["id"], "title": row.get("title") or row["id"], "title_en": row.get("title_en") or row.get("title") or row["id"],
                    "icon": row.get("icon") or "", "accent": row.get("accent") or "", "enabled": bool(row.get("enabled")),
                    "builtin": bool(row.get("builtin")), "permissions": list(row.get("permissions") or []),
                    "panels": clean.get("panels", []), "commands": clean.get("commands", []),
                    "widgets": clean.get("widgets", []), "skill": clean.get("skill") or {}, "folder": str(folder)})
    return out


def find(module_id: str, *, root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    return next((row for row in contributions(root) if row["module"] == module_id), None)


# ------------------------------------------------------------------------------------------- settings storage

def _settings_file(root: Path) -> Path:
    return root / "integration.json"


def load_settings(root: Optional[Path] = None) -> Dict[str, Any]:
    path = _settings_file(_root(root))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _clean_value(spec: Dict[str, Any], value: Any) -> Any:
    if spec["type"] == "bool":
        return value is True or str(value).lower() in ("1", "true", "on")
    if spec["type"] == "choice":
        return str(value) if str(value) in [c["value"] for c in spec["choices"]] else spec["default"]
    if spec["type"] == "color":
        return str(value) if re.fullmatch(r"#[0-9a-fA-F]{6}|auto|accent|cover", str(value)) else spec["default"]
    return _text(value, 200)


def item_settings(module_id: str, kind: str, item_id: str, *, root: Optional[Path] = None) -> Dict[str, Any]:
    """Effective settings (defaults + saved) of one widget or panel of an enabled module."""
    row = find(module_id, root=root)
    item = next((x for x in (row or {}).get(kind + "s", []) if x["id"] == item_id), None)
    if item is None:
        raise KeyError(item_id)
    saved = load_settings(root).get(f"{module_id}:{kind}:{item_id}") or {}
    return {spec["key"]: _clean_value(spec, saved[spec["key"]]) if spec["key"] in saved else spec["default"]
            for spec in item.get("settings", [])}


def save_item_settings(module_id: str, kind: str, item_id: str, values: Dict[str, Any], *,
                       root: Optional[Path] = None) -> Dict[str, Any]:
    root = _root(root)
    row = find(module_id, root=root)
    item = next((x for x in (row or {}).get(kind + "s", []) if x["id"] == item_id), None)
    if item is None:
        raise KeyError(item_id)
    specs = {spec["key"]: spec for spec in item.get("settings", [])}
    with _LOCK:
        data = load_settings(root)
        key = f"{module_id}:{kind}:{item_id}"
        current = dict(data.get(key) or {})
        for name, value in (values or {}).items():
            if name in specs:
                current[name] = _clean_value(specs[name], value)
        data[key] = current
        path = _settings_file(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    return item_settings(module_id, kind, item_id, root=root)


def public(root: Optional[Path] = None, lang: str = "ru") -> Dict[str, Any]:
    """What the windows need: panels for the gallery, commands for local matching, widgets with their settings."""
    root = _root(root)
    en = lang == "en"
    modules = []
    for row in contributions(root):
        widgets = [{**w, "values": item_settings(row["module"], "widget", w["id"], root=root)} for w in row["widgets"]]
        modules.append({"module": row["module"], "title": row["title_en"] if en else row["title"], "icon": row["icon"],
                        "accent": row["accent"], "permissions": row["permissions"],
                        "panels": [{k: p[k] for k in ("id", "title", "title_en", "summary", "summary_en", "icon", "size", "height", "settings")}
                                   for p in row["panels"]],
                        "commands": [{k: c[k] for k in ("id", "title", "title_en", "description", "description_en", "args",
                                                        "phrases", "phrases_en", "danger")} for c in row["commands"]],
                        "widgets": widgets})
    return {"modules": modules}


# ------------------------------------------------------------------------------------------- panel documents

PANEL_PRELUDE = r"""
(function () {
  "use strict";
  // Round 38: a module panel. Memu.panel.* — its id, settings, open the module, report its height.
  var params = {}; try { params = JSON.parse(decodeURIComponent((location.hash || "#").slice(1)) || "{}"); } catch (e) { params = {}; }
  var M = window.Memu;
  if (!M) return;
  M.panel = {
    id: __PANEL_ID__, settings: __PANEL_SETTINGS__, params: params,
    open: function (detail) { return parent.postMessage({ type: "memu-module", id: "panel-open-" + Date.now(), from: __MODULE_ID__, action: "open_module", payload: detail || {} }, "*"); },
    data: function (args) {
      // Round 38: a built-in panel's server data (contributes.panels[].data → module_integration_data.py).
      var id = "panel-data-" + Date.now() + "-" + Math.random().toString(36).slice(2, 8);
      return new Promise(function (resolve, reject) {
        var done = function (event) {
          var d = event.data || {};
          if (d.type !== "memu-module-result" || d.id !== id) return;
          window.removeEventListener("message", done);
          if (d.error) reject(new Error(d.error)); else resolve(d.result);
        };
        window.addEventListener("message", done);
        parent.postMessage({ type: "memu-module", id: id, from: __MODULE_ID__, action: "panel_data", payload: { panel: __PANEL_ID__, args: args || {} } }, "*");
      });
    },
    height: function (px) { try { parent.postMessage({ type: "memu-panel-height", from: __MODULE_ID__, height: Math.round(+px || document.documentElement.scrollHeight) }, "*"); } catch (e) {} },
    visible: true
  };
  window.addEventListener("message", function (event) {
    var data = event.data || {};
    if (data.type === "memu-module-host" && data.action === "theme" && data.payload && data.payload.theme) {
      // The same colour scheme as the MemuAI window — otherwise the browser paints an opaque backdrop behind the panel.
      document.documentElement.style.colorScheme = data.payload.theme === "light" ? "light" : "dark";
    }
    if (data.type === "memu-panel-visible") {
      M.panel.visible = !!data.visible;
      try { document.dispatchEvent(new CustomEvent("memu-panel-visible", { detail: { visible: M.panel.visible } })); } catch (e) {}
    }
  });
  document.documentElement.setAttribute("data-memu-panel", __PANEL_ID__);
  if (params.size) document.documentElement.setAttribute("data-size", String(params.size));
})();
"""


PANEL_KIT_CSS = Path(__file__).parent / "pgstatic" / "module-panel-kit.css"   # the shared look of every module panel


def panel_document(module_id: str, panel_id: str, *, env: Optional[Dict[str, Any]] = None,
                   root: Optional[Path] = None) -> str:
    """The sandbox page of one module panel: the module's bridge + its panel HTML/JS/CSS (no network, same CSP)."""
    root = _root(root)
    row = find(module_id, root=root)
    panel = next((p for p in (row or {}).get("panels", []) if p["id"] == panel_id), None)
    if row is None or panel is None:
        raise KeyError(f"{module_id}:{panel_id}")
    folder = Path(row["folder"])
    fallback = modules_host.builtin_dir() / module_id if row.get("builtin") else None

    def read(name: str) -> str:
        for base in (folder, fallback):
            if base is not None and (base / name).is_file():
                return (base / name).read_text(encoding="utf-8")
        raise KeyError(name)

    package = modules_host.load_package(root, module_id)
    settings = item_settings(module_id, "panel", panel_id, root=root)
    prelude = (PANEL_PRELUDE.replace("__PANEL_ID__", modules_host._script_json(panel_id))
               .replace("__PANEL_SETTINGS__", modules_host._script_json(settings))
               .replace("__MODULE_ID__", modules_host._script_json(module_id)))
    page = dict(package)
    page["html"] = read(panel["entry"])
    page["css"] = (PANEL_KIT_CSS.read_text(encoding="utf-8") + "\n" + "\n".join(read(name) for name in panel["styles"])
                   + "\nhtml,body{background:transparent;overflow:hidden}\n")
    page["js"] = prelude + "\n" + "\n".join(read(name) for name in panel["scripts"])
    return modules_host.module_document(page, {}, env={**(env or {}), "toolbar": ""})


DataHandler = Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]
DATA: Dict[str, DataHandler] = {}


def data_source(name: str):
    def wrap(fn: DataHandler) -> DataHandler:
        DATA[name] = fn
        return fn
    return wrap


def panel_data(module_id: str, panel_id: str, args: Any = None, *, lang: str = "ru",
               root: Optional[Path] = None) -> Dict[str, Any]:
    """Server data of a built-in module panel (``contributes.panels[].data``): read-only, the module must be on."""
    root = _root(root)
    row = find(module_id, root=root)
    panel = next((p for p in (row or {}).get("panels", []) if p["id"] == panel_id), None)
    fn = DATA.get((panel or {}).get("data") or "")
    if row is None or fn is None:
        raise KeyError(f"{module_id}:{panel_id}")
    en = lang == "en"
    return fn(args if isinstance(args, dict) else {}, {"lang": lang, "en": en, "root": root, "module": row,
                                                        "tr": (lambda ru, e: e if en else ru)})


# ---------------------------------------------------------------------------------------------- skill for the AI

def _skill_text(row: Dict[str, Any], en: bool) -> str:
    skill = row.get("skill") or {}
    if skill.get("file"):
        try:
            return (Path(row["folder"]) / skill["file"]).read_text(encoding="utf-8")[:6000].strip()
        except OSError:
            return ""
    return str(skill.get("text_en" if en else "text") or "").strip()


def command_line(row: Dict[str, Any], cmd: Dict[str, Any], en: bool) -> str:
    props = cmd["args"].get("properties") or {}
    args = ", ".join(f"{k}{'' if k in cmd['args'].get('required', []) else '?'}: {v['type']}"
                     + (f" ({'|'.join(v['enum'])})" if v.get("enum") else "") + (f" — {v['description']}" if v.get("description") else "")
                     for k, v in props.items())
    desc = cmd["description_en"] if en else cmd["description"]
    phrases = cmd["phrases_en"] if en else cmd["phrases"]
    return (f"- {row['module']}.{cmd['id']} {{{args}}} — {desc or cmd['title']}"
            + (f" (e.g. «{'», «'.join(phrases[:3])}»)" if phrases else "") + (" [asks the user to confirm]" if cmd["danger"] else ""))


def skill_prompt(root: Optional[Path] = None, *, lang: str = "ru", surface: str = "chat") -> str:
    """The module commands block for the system prompt (empty when no enabled module has commands)."""
    en = lang == "en"
    rows = [row for row in contributions(root) if row["commands"]]
    if not rows:
        return ""
    lines = ["<module_commands>",
             "Enabled MemuAI modules can be controlled with the tool module_command {module, command, args}. Use it when the user "
             "asks for one of these actions (play music, open a character…) — do the action instead of explaining how. "
             "The result card is shown to the user automatically: answer in one short sentence afterwards, do not repeat it.",
             "Write the call in the usual hidden ```memuai-tool``` JSON block: [{\"tool\":\"module_command\",\"args\":"
             "{\"module\":\"music\",\"command\":\"play\",\"args\":{\"query\":\"Imagine Dragons Believer\"}}}]"]
    for row in rows:
        lines.append(f"\n{row['title_en'] if en else row['title']} (module {row['module']}):")
        lines.extend(command_line(row, cmd, en) for cmd in row["commands"])
        text = _skill_text(row, en)
        if text:
            lines.append(text)
    lines.append("</module_commands>")
    return "\n".join(lines)


def alias_tool(name: str, args: Any = None, root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Round 39 (r39-integ): a model that names the module command as the tool («music_play», «music.play», «music:next»)
    instead of ``module_command {module, command}`` — the call is turned into module_command, not reported as a missing tool."""
    norm = lambda v: re.sub(r"[^a-z0-9]+", "_", str(v or "").lower()).strip("_")
    want = norm(name)
    if not want or want == "module_command":
        return None
    try:
        rows = contributions(root)
    except Exception:  # noqa: BLE001 - a broken module never breaks a tool call
        return None
    for row in rows:
        for cmd in row["commands"]:
            if want in (norm(row["module"] + "_" + cmd["id"]), norm(row["module"] + "_" + cmd["id"]).replace("_", "")):
                params = dict(args) if isinstance(args, dict) else {}
                inner = params.pop("args", None)
                params.pop("module", None)
                params.pop("command", None)
                return {"tool": "module_command", "args": {"module": row["module"], "command": cmd["id"],
                                                           "args": inner if isinstance(inner, dict) else params}}
    return None


# --------------------------------------------------------------------------------------------- command runtime

Handler = Callable[[Dict[str, Any], Dict[str, Any]], Awaitable[Dict[str, Any]]]
HANDLERS: Dict[str, Handler] = {}


def handler(name: str):
    def wrap(fn: Handler) -> Handler:
        HANDLERS[name] = fn
        return fn
    return wrap


class CommandError(Exception):
    pass


def _check_args(cmd: Dict[str, Any], args: Any) -> Dict[str, Any]:
    args = args if isinstance(args, dict) else {}
    props = cmd["args"].get("properties") or {}
    out: Dict[str, Any] = {}
    for key, spec in props.items():
        if key not in args or args[key] in (None, ""):
            continue
        value = args[key]
        if spec["type"] in ("number", "integer"):
            try:
                value = int(value) if spec["type"] == "integer" else float(value)
            except (TypeError, ValueError):
                raise CommandError(f"{key}: ожидается число / a number is expected")
        elif spec["type"] == "boolean":
            value = value is True or str(value).lower() in ("1", "true", "yes", "да")
        else:
            value = str(value)[:500]
            if spec.get("enum") and value not in spec["enum"]:
                raise CommandError(f"{key}: одно из {spec['enum']} / one of {spec['enum']}")
        out[key] = value
    missing = [key for key in cmd["args"].get("required", []) if key not in out]
    if missing:
        raise CommandError(f"Не хватает аргументов: {', '.join(missing)} / Missing arguments: {', '.join(missing)}")
    return out


async def run_command(module_id: str, command: str, args: Any = None, *, lang: str = "ru", confirmed: bool = False,
                      surface: str = "chat", root: Optional[Path] = None, base: str = "") -> Dict[str, Any]:
    """Run ``module.command``. Returns ``{ok, text, card?, client?, needs_confirm?}`` — never raises for user errors."""
    en = lang == "en"
    tr = (lambda ru, e: e if en else ru)  # noqa: E731
    row = find(str(module_id or ""), root=root)
    if row is None:
        return {"ok": False, "error": tr(f"Модуль «{module_id}» не включён или не добавляет команд",
                                         f"The module “{module_id}” is off or adds no commands")}
    cmd = next((c for c in row["commands"] if c["id"] == str(command or "")), None)
    if cmd is None:
        known = ", ".join(c["id"] for c in row["commands"])
        return {"ok": False, "error": tr(f"У модуля нет команды «{command}». Есть: {known}",
                                         f"The module has no command “{command}”. Available: {known}")}
    try:
        clean = _check_args(cmd, args)
    except CommandError as exc:
        return {"ok": False, "error": str(exc)}
    if cmd["danger"] and not confirmed:
        return {"ok": False, "needs_confirm": True, "module": row["module"], "command": cmd["id"], "args": clean,
                "title": cmd["title_en"] if en else cmd["title"],
                "error": tr("Нужно подтверждение пользователя", "The user has to confirm this")}
    title = row["title_en"] if en else row["title"]
    if cmd["run"].get("open"):
        return {"ok": True, "text": tr(f"Открываю {title}", f"Opening {title}"),
                "client": {"open_module": row["module"], "detail": {"command": cmd["id"], "args": clean, "from": surface}}}
    fn = HANDLERS.get(cmd["run"]["handler"])
    if fn is None:
        return {"ok": False, "error": tr("Обработчик команды недоступен", "The command handler is unavailable")}
    ctx = {"lang": lang, "en": en, "tr": tr, "module": row, "root": _root(root), "surface": surface, "base": base}
    try:
        result = await fn(clean, ctx)
    except (KeyError, ValueError, CommandError) as exc:
        return {"ok": False, "error": str(exc.args[0] if exc.args else exc)}
    result.setdefault("ok", True)
    result.setdefault("module", row["module"])
    result.setdefault("command", cmd["id"])
    return result


# --------------------------------------------------------------------------------------------- phrase matching

def _phrase_regex(phrase: str) -> Optional[Tuple["re.Pattern", List[str]]]:
    names = re.findall(r"\{([a-z_][a-z0-9_]*)\}", phrase)
    parts = re.split(r"\{[a-z_][a-z0-9_]*\}", phrase.strip().lower())
    pattern = "".join(re.escape(part).replace(r"\ ", r"\s+") + ("(.+?)" if i < len(names) else "")
                      for i, part in enumerate(parts))
    try:
        return re.compile(r"^\s*" + pattern + r"\s*[.!?]*\s*$", re.I), names
    except re.error:
        return None


def match_phrase(text: str, root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """A request that matches a declared phrase exactly («включи трек Believer») → {module, command, args}."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text or len(text) > 200:
        return None
    for row in contributions(root):
        for cmd in row["commands"]:
            for phrase in cmd["phrases"] + cmd["phrases_en"]:
                compiled = _phrase_regex(phrase)
                if not compiled:
                    continue
                found = compiled[0].match(text)
                if found:
                    return {"module": row["module"], "command": cmd["id"],
                            "args": {name: found.group(i + 1).strip(" «»\"'") for i, name in enumerate(compiled[1])}}
    return None


from . import module_integration_handlers  # noqa: E402,F401  (registers the built-in handlers)
from . import module_integration_data  # noqa: E402,F401  (registers the built-in panel data sources)
