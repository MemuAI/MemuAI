"""Round 27: «Быстрые действия» — the user's own round buttons in Quick Ask (plugin ``canvas``).

A button is one action or a macro: a chain of steps. Every step runs HERE, on the server, through the
existing executors — :func:`app.assistant_commands.engine` (``Engine.run``: catalogue validation, the
``safe``/``confirm``/``dangerous`` tiers, rate limits, the audit log) — never through a shell of its own.
PowerShell exists only as the explicit ``shell`` step, which is the engine's ``shell.run``: «dangerous»
(confirmed with a countdown on every run) and off until the user enables the advanced mode.

Step types (``validate_step``):

==========  ===============================================================================================
open        ``target`` app | url | path | folder | settings, ``value`` → app.open / open.url / open.path /
            open.folder / open.settings
command     ``action`` (any public catalogue id, :mod:`app.assistant_catalog`), ``params`` {…}
ask         ``prompt`` (template), ``mode`` window (the answer streams in the Quick Ask window — the page sends
            it and reports the answer back) | silent (one model answer here → ``{prev}``)
text        ``text`` (template), ``mode`` paste (into the program the window was opened over) | copy | input
            (into the Quick Ask field)
system      ``op`` screenshot | lock | dnd | theme | mute | minimize_all | media, ``value``
dfy         ``goal`` → dfy.start (the page hands the confirmed task to the Do-For-You agent)
computer    ``task`` → computer.use (the page runs Computer Use, every step asks for approval)
memuai      ``target`` (main, new_chat, panels, settings…) | chat with ``text`` → memuai.open / memuai.chat
wait        ``ms`` (50 ms … 10 min)
keys        ``keys`` → keys.press («confirm»)
shell       ``command`` → shell.run («dangerous», advanced mode only)
==========  ===============================================================================================

Every step may have ``label``, ``continue_on_error`` and ``confirm`` (ask before this step on every run).
Templates (``render``): ``{text}`` (the selection, else the clipboard) ``{clipboard} {selection} {input} {prev} {date} {time} {datetime} {weekday}
{page_url} {page_title}``. ``{selection}`` is the text the r27 selection capture put into the window
(``MemuQuick.selection()``), sent by the page with the run request.

**Safety.** A button whose steps include a ``confirm``/``dangerous`` action (or a risky file, keys, shell,
DFY, Computer Use) runs for the first time only after the user approved the list of those steps
(``approval_needed`` → «Выполнить и запомнить»); the approval is the SHA-256 of the steps, so editing a
step asks again. Approved ``confirm`` steps then run without a second question; ``dangerous`` ones
(power, PowerShell) always go through the engine's own confirmation card, and so does any risky step whose
parameters come from a template (``{clipboard}`` may hold anything). Nothing runs when the window opens:
the page starts a run only on a click / Enter / the button's hotkey.

Runs (``Runner``) are kept in memory (last 30), progress is polled by the page
(``GET /api/quick/canvas/runs/{id}``), ``cancel`` stops between steps and interrupts waits.
"""
from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import logging
import re
import sys
import time
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("memuai.quick.actions")

MAX_ACTIONS = 64
MAX_STEPS = 30
MAX_LABEL = 40
MAX_IMAGE_BYTES = 96 * 1024          # a data: URL of the button picture (the page scales it to 128 px)
SHAPES = ("circle", "squircle")
STEP_TYPES = ("open", "command", "ask", "text", "system", "dfy", "computer", "memuai", "wait", "keys", "shell")
OPEN_TARGETS = ("app", "url", "path", "folder", "settings")
SYSTEM_OPS = ("screenshot", "lock", "dnd", "theme", "mute", "minimize_all", "media")
ASK_MODES = ("window", "silent")
TEXT_MODES = ("paste", "copy", "input")
PLACEHOLDERS = ("text", "clipboard", "selection", "input", "prev", "date", "time", "datetime", "weekday", "page_url", "page_title")
PLACEHOLDER_RE = re.compile(r"\{(" + "|".join(PLACEHOLDERS) + r")\}")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
ID_RE = re.compile(r"^a_[a-z0-9]{4,24}$")
ICON_NAME_RE = re.compile(r"^[a-z0-9-]{1,32}$")
IMAGE_RE = re.compile(r"^data:image/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+$")
#: Combos the Quick Ask window (quick.js and its plugins) already uses — a button can not take them.
RESERVED_HOTKEYS = {
    "Ctrl+N", "Ctrl+H", "Ctrl+P", "Ctrl+K", "Ctrl+B", "Ctrl+E", "Ctrl+Enter", "Ctrl+Shift+Enter", "Ctrl+Shift+C",
    "Ctrl+Shift+A", "Ctrl+Shift+S", "Ctrl+Shift+X", "Ctrl+Shift+P", "Ctrl+Space", "Alt+M", "Alt+R", "Alt+V",
    "Alt+Enter", "Ctrl+A", "Ctrl+C", "Ctrl+V", "Ctrl+X", "Ctrl+Z", "Ctrl+Y",
}
#: Round 34: combos a GLOBAL button hotkey may not take — every program relies on them (save, close, find, tabs…).
GLOBAL_TAKEN = {
    "Alt+F4", "Ctrl+F4", "Ctrl+S", "Ctrl+O", "Ctrl+W", "Ctrl+T", "Ctrl+F", "Ctrl+R", "Ctrl+L", "Ctrl+D", "Ctrl+I",
    "Ctrl+U", "Ctrl+G", "Ctrl+J", "Ctrl+Q", "Ctrl+Shift+T", "Ctrl+Shift+N", "Ctrl+Shift+Z", "Ctrl+Shift+V",
    "Ctrl+Shift+I", "Ctrl+Shift+F", "Alt+F", "Alt+E", "Alt+D", "Ctrl+F5",
}
HOTKEY_KEY_RE = re.compile(r"^(?:[A-Z0-9]|F(?:[1-9]|1[0-9]|2[0-4]))$")
WEEKDAYS = {"ru": ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"],
            "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]}


def tr(lang: str, ru: str, en: str) -> str:
    return en if lang == "en" else ru


class ActionInvalid(ValueError):
    """Bad button / step data; ``text(lang)`` is the message for the editor."""

    def __init__(self, ru: str, en: str = "") -> None:
        super().__init__(ru)
        self.ru, self.en = ru, en or ru

    def text(self, lang: str = "ru") -> str:
        return self.en if lang == "en" else self.ru


def _catalog():
    """The full catalogue: the engine binds the Round 24/25 actions into it on creation."""
    from . import assistant_catalog as catalog
    from . import assistant_commands
    assistant_commands.engine()
    return catalog


def _str(value: Any, limit: int) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        raise ActionInvalid("Ожидался текст", "Text expected")
    return value.replace("\x00", "").strip()[:limit]


def _bool(value: Any) -> bool:
    return value is True or value in (1, "1", "true", "on", "yes")


def _int(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, number))


# ------------------------------------------------------------------ hotkeys

def normalize_hotkey(raw: Any) -> str:
    """``"shift+ctrl+1"`` → ``"Ctrl+Shift+1"``; ``""`` = none. Needs Ctrl or Alt (letters alone are typing)."""
    text = _str(raw, 40)
    if not text:
        return ""
    parts = [p.strip() for p in text.replace(" ", "").split("+") if p.strip()]
    mods = {"ctrl": False, "alt": False, "shift": False}
    key = ""
    for part in parts:
        low = part.lower()
        if low in ("ctrl", "control", "ctl"):
            mods["ctrl"] = True
        elif low in ("alt", "option"):
            mods["alt"] = True
        elif low == "shift":
            mods["shift"] = True
        elif not key:
            key = part.upper()
        else:
            raise ActionInvalid("В сочетании может быть только одна клавиша", "A combo can have only one key")
    if not key or not HOTKEY_KEY_RE.match(key):
        raise ActionInvalid("Клавиша: буква, цифра или F1–F24", "Key: a letter, a digit or F1–F24")
    if not (mods["ctrl"] or mods["alt"]) and not key.startswith("F"):
        raise ActionInvalid("Нужен Ctrl или Alt — иначе клавиша мешала бы печатать",
                            "Add Ctrl or Alt — otherwise the key would get in the way of typing")
    combo = "+".join([m.capitalize() for m in ("ctrl", "alt", "shift") if mods[m]] + [key])
    if combo in RESERVED_HOTKEYS or (mods["alt"] and not mods["ctrl"] and not mods["shift"] and key.isdigit()):
        raise ActionInvalid(f"{combo} уже занято окном быстрого доступа", f"{combo} is already used by the Quick Ask window")
    return combo


# ------------------------------------------------------------------ validation

def validate_icon(raw: Any) -> Dict[str, str]:
    raw = raw if isinstance(raw, dict) else {"kind": "builtin", "value": "bolt"}
    kind = str(raw.get("kind") or "builtin")
    value = raw.get("value")
    if kind == "builtin":
        name = _str(value, 32) or "bolt"
        if not ICON_NAME_RE.match(name):
            raise ActionInvalid("Неизвестная иконка", "Unknown icon")
        return {"kind": "builtin", "value": name}
    if kind == "emoji":
        text = _str(value, 16)
        if not text or re.search(r"[<>&\"'\\]", text) or len(text) > 16:
            raise ActionInvalid("Эмодзи: один символ или короткий значок", "Emoji: one symbol or a short mark")
        return {"kind": "emoji", "value": text}
    if kind in ("image", "app"):
        data = str(value or "")
        if not IMAGE_RE.match(data):
            raise ActionInvalid("Картинка: PNG, JPEG, WebP или GIF", "Picture: PNG, JPEG, WebP or GIF")
        if len(data) > MAX_IMAGE_BYTES * 4 // 3 + 64:
            raise ActionInvalid("Картинка слишком большая (до 96 КБ)", "The picture is too large (up to 96 KB)")
        out = {"kind": kind, "value": data}
        if kind == "app":
            out["app"] = _str(raw.get("app"), 120)
        return out
    raise ActionInvalid("Неизвестный вид иконки", "Unknown icon kind")


def _params(action_id: str, raw: Any) -> Dict[str, Any]:
    """Catalogue params with templates allowed: keys must exist, values are scalars (types checked at run time)."""
    catalog = _catalog()
    row = catalog.get(action_id)
    raw = raw if isinstance(raw, dict) else {}
    out: Dict[str, Any] = {}
    for key, value in raw.items():
        if key not in row["params"] or value in (None, ""):
            continue
        if isinstance(value, bool) or (isinstance(value, (int, float)) and value == value):
            out[key] = value
        elif isinstance(value, str):
            out[key] = value.replace("\x00", "")[:20000]
        else:
            raise ActionInvalid(f"Параметр {key}: нужно значение, а не список", f"Parameter {key}: a value, not a list")
    # without templates the full validation can run now (bad enums / numbers are caught in the editor)
    if not any(isinstance(v, str) and PLACEHOLDER_RE.search(v) for v in out.values()):
        try:
            catalog.validate_params(action_id, out)
        except catalog.ParamError as exc:
            raise ActionInvalid("Неверные параметры: " + str(exc), "Bad parameters: " + str(exc)) from None
    else:
        missing = [k for k in row["required"] if k not in out]
        if missing:
            raise ActionInvalid("Не хватает: " + ", ".join(missing), "Missing: " + ", ".join(missing))
    return out


def validate_step(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ActionInvalid("Шаг должен быть объектом", "A step must be an object")
    kind = str(raw.get("type") or "")
    if kind not in STEP_TYPES:
        raise ActionInvalid("Неизвестный тип шага", "Unknown step type")
    step: Dict[str, Any] = {"type": kind}
    if kind == "open":
        target = str(raw.get("target") or "app")
        if target not in OPEN_TARGETS:
            raise ActionInvalid("Что открыть: программу, ссылку, файл, папку или параметры", "Open: an app, link, file, folder or settings")
        value = _str(raw.get("value"), 2000)
        catalog = _catalog()
        if target == "folder" and value not in catalog.FOLDERS and not PLACEHOLDER_RE.search(value):
            raise ActionInvalid("Неизвестная папка", "Unknown folder")
        if target == "settings" and value not in catalog.SETTINGS_PAGES:
            raise ActionInvalid("Неизвестная страница параметров", "Unknown settings page")
        if not value and target != "settings":
            raise ActionInvalid("Укажите, что открыть", "Say what to open")
        if target == "url" and not PLACEHOLDER_RE.search(value):
            from .assistant_commands import ActionError, normalize_url
            try:
                normalize_url(value)
            except ActionError:
                raise ActionInvalid("Ссылка: только http(s) или адрес сайта", "Link: only http(s) or a site address") from None
        step.update(target=target, value=value)
    elif kind == "command":
        catalog = _catalog()
        action_id = str(raw.get("action") or "")
        if action_id.startswith("_") or not catalog.get(action_id):
            raise ActionInvalid("Неизвестная команда", "Unknown command")
        step.update(action=action_id, params=_params(action_id, raw.get("params")))
    elif kind == "ask":
        prompt = _str(raw.get("prompt"), 8000)
        if not prompt:
            raise ActionInvalid("Напишите запрос для ИИ", "Write the request for the AI")
        mode = str(raw.get("mode") or "window")
        step.update(prompt=prompt, mode=mode if mode in ASK_MODES else "window")
    elif kind == "text":
        text = _str(raw.get("text"), 20000)
        if not text:
            raise ActionInvalid("Пустой текст", "Empty text")
        mode = str(raw.get("mode") or "paste")
        step.update(text=text, mode=mode if mode in TEXT_MODES else "paste")
    elif kind == "system":
        op = str(raw.get("op") or "")
        if op not in SYSTEM_OPS:
            raise ActionInvalid("Неизвестное системное действие", "Unknown system action")
        value = str(raw.get("value") or "")
        allowed = {"dnd": ("toggle", "on", "off"), "theme": ("toggle", "dark", "light"), "mute": ("toggle", "on", "off"),
                   "screenshot": ("screen", "region", "window"), "media": ("play_pause", "next", "prev", "stop")}.get(op)
        if allowed:
            value = value if value in allowed else allowed[0]
        else:
            value = ""
        step.update(op=op, value=value)
    elif kind == "dfy":
        goal = _str(raw.get("goal"), 4000)
        if not goal:
            raise ActionInvalid("Опишите задачу", "Describe the task")
        step["goal"] = goal
    elif kind == "computer":
        task = _str(raw.get("task"), 4000)
        if not task:
            raise ActionInvalid("Опишите задачу", "Describe the task")
        step["task"] = task
    elif kind == "memuai":
        target = str(raw.get("target") or "main")
        catalog = _catalog()
        if target != "chat" and target not in catalog.MEMUAI_TARGETS:
            raise ActionInvalid("Неизвестный раздел MemuAI", "Unknown MemuAI section")
        step["target"] = target
        if target == "chat":
            text = _str(raw.get("text"), 8000)
            if not text:
                raise ActionInvalid("Напишите текст для чата", "Write the text for the chat")
            step["text"] = text
    elif kind == "wait":
        step["ms"] = _int(raw.get("ms"), 50, 600000, 1000)
    elif kind == "keys":
        keys = _str(raw.get("keys"), 80)
        if not keys:
            raise ActionInvalid("Укажите сочетание, например Ctrl+Shift+Esc", "Give a combo, e.g. Ctrl+Shift+Esc")
        step["keys"] = keys
    elif kind == "shell":
        command = _str(raw.get("command"), 4000)
        if not command:
            raise ActionInvalid("Пустая команда", "Empty command")
        step["command"] = command
    label = _str(raw.get("label"), 60)
    if label:
        step["label"] = label
    if _bool(raw.get("continue_on_error")):
        step["continue_on_error"] = True
    if _bool(raw.get("confirm")):
        step["confirm"] = True
    return step


def new_id() -> str:
    return "a_" + uuid.uuid4().hex[:10]


def validate_action(raw: Any, *, keep_meta: bool = True) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ActionInvalid("Кнопка должна быть объектом", "A button must be an object")
    label = _str(raw.get("label"), MAX_LABEL)
    steps_raw = raw.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise ActionInvalid("Добавьте хотя бы одно действие", "Add at least one action")
    if len(steps_raw) > MAX_STEPS:
        raise ActionInvalid(f"Не больше {MAX_STEPS} шагов", f"At most {MAX_STEPS} steps")
    steps = []
    for index, row in enumerate(steps_raw):
        try:
            steps.append(validate_step(row))
        except ActionInvalid as exc:
            raise ActionInvalid(f"Шаг {index + 1}: {exc.ru}", f"Step {index + 1}: {exc.en}") from None
    action_id = str(raw.get("id") or "")
    if not ID_RE.match(action_id):
        action_id = new_id()
    color = str(raw.get("color") or "")
    color2 = str(raw.get("color2") or "")
    hotkey = normalize_hotkey(raw.get("hotkey"))
    global_hotkey = bool(hotkey) and _bool(raw.get("global"))
    if global_hotkey and not re.match(r"^(Ctrl|Alt)\+", hotkey):
        raise ActionInvalid("Для работы во всех программах нужно сочетание с Ctrl или Alt",
                            "A hotkey for every program needs Ctrl or Alt")
    if global_hotkey:
        # Round 34: a global Alt+F4 / Ctrl+S / Ctrl+W took the combo away from EVERY program (RegisterHotKey swallows
        # it) — the same rules as the Quick Ask hotkey itself, plus the browser/editor staples below
        from .quick_features import hotkey_error
        if hotkey_error(hotkey, "ru"):
            raise ActionInvalid(hotkey_error(hotkey, "ru"), hotkey_error(hotkey, "en"))
    if global_hotkey and hotkey in GLOBAL_TAKEN:
        raise ActionInvalid(f"{hotkey} — стандартное сочетание Windows и программ; во всех программах его занимать нельзя",
                            f"{hotkey} is a standard Windows/app shortcut; it cannot be taken in every program")
    now = time.time()
    out = {
        "id": action_id,
        "label": label,
        "tooltip": _str(raw.get("tooltip"), 200),
        "icon": validate_icon(raw.get("icon")),
        "color": color if HEX_RE.match(color) else "",
        "color2": color2 if HEX_RE.match(color2) and HEX_RE.match(color) else "",
        "shape": raw.get("shape") if raw.get("shape") in SHAPES else "circle",
        "hotkey": hotkey,
        "global": global_hotkey,              # r27-hunt-canvas: the hotkey works in every program (win_hotkey)
        "steps": steps,
        "approved": str(raw.get("approved") or "")[:64] if keep_meta else "",
        "created": float(raw.get("created") or now) if keep_meta and isinstance(raw.get("created"), (int, float)) else now,
        "updated": now,
    }
    return out


def signature(action: Dict[str, Any]) -> str:
    """What the user approved: the steps exactly (labels and looks do not matter)."""
    body = json.dumps(action.get("steps") or [], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ steps → engine

def engine_call(step: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, Any]]]:
    """The catalogue action a step runs as (``None`` for steps handled here or by the page)."""
    kind = step["type"]
    if kind == "open":
        target, value = step["target"], step.get("value", "")
        return {"app": ("app.open", {"name": value}), "url": ("open.url", {"url": value}),
                "path": ("open.path", {"path": value}), "folder": ("open.folder", {"folder": value}),
                "settings": ("open.settings", {"page": value} if value else {})}[target]
    if kind == "command":
        return step["action"], dict(step.get("params") or {})
    if kind == "system":
        op, value = step["op"], step.get("value", "")
        if op == "screenshot":
            return "screenshot.take", {"mode": value or "screen", "copy": True}
        if op == "lock":
            return "lock.screen", {}
        if op == "theme":
            return "theme.set", {"mode": value or "toggle"}
        if op == "mute":
            return "volume.mute", {} if value in ("", "toggle") else {"mute": value == "on"}
        if op == "minimize_all":
            return "window.minimize_all", {}
        if op == "media":
            return "media.key", {"key": value or "play_pause"}
        return None                       # dnd — :func:`set_dnd`
    if kind == "dfy":
        return "dfy.start", {"goal": step["goal"]}
    if kind == "computer":
        return "computer.use", {"task": step["task"]}
    if kind == "memuai":
        if step["target"] == "chat":
            return "memuai.chat", {"text": step.get("text", "")}
        return "memuai.open", {"target": step["target"]}
    if kind == "keys":
        return "keys.press", {"keys": step["keys"]}
    if kind == "shell":
        return "shell.run", {"command": step["command"]}
    if kind == "text" and step.get("mode") == "copy":
        return "clipboard.set", {"text": step["text"]}
    return None


def _has_template(params: Dict[str, Any]) -> bool:
    return any(isinstance(v, str) and PLACEHOLDER_RE.search(v) for v in params.values())


def step_tier(step: Dict[str, Any], settings: Optional[Dict[str, Any]] = None, params: Optional[Dict[str, Any]] = None) -> str:
    """safe | confirm | dangerous — the engine's tier of the step (with its ``always_confirm``/``open.path`` rules)."""
    call = engine_call(step)
    if call is None:
        return "safe"
    action_id, base = call
    params = base if params is None else params
    catalog = _catalog()
    row = catalog.get(action_id) or {}
    tier = row.get("tier", "safe")
    if action_id == "open.path":
        from .assistant_commands import risky_path
        path = str(params.get("path", ""))
        if PLACEHOLDER_RE.search(path) or risky_path(path):
            tier = "confirm"
    elif action_id == "command.run":
        # r27-hunt-canvas: the engine runs «Свои команды» with the tier of their steps (a «confirm» routine asked on
        # every run while the first-run approval list did not even mention it)
        tier = _command_tier(params) or tier
    if tier == "safe" and settings and action_id in (settings.get("always_confirm") or []):
        tier = "confirm"
    return tier


def _command_tier(params: Dict[str, Any]) -> str:
    """The tier the engine gives ``command.run`` of the user's own command (``""`` — unknown here)."""
    ref = str(params.get("id") or params.get("name") or "")
    if not ref or PLACEHOLDER_RE.search(ref):
        return ""
    try:
        from . import assistant_commands
        eng = assistant_commands.engine()
        command = eng._find_command(ref)
        if not command:
            return ""
        return assistant_commands.command_tier(command, eng._find_command)
    except Exception:                            # noqa: BLE001 - the engine decides at run time anyway
        return ""


def risks(action: Dict[str, Any], lang: str = "ru", settings: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Steps that need the user's word before the button runs for the first time. The text is what the step
    really does — never only its label: an imported button could call «Ctrl+Alt+Del» «Открыть калькулятор»."""
    out = []
    for index, step in enumerate(action.get("steps") or []):
        tier = step_tier(step, settings)
        if tier != "safe":
            real = describe_step({k: v for k, v in step.items() if k != "label"}, lang)
            text = f"{real} («{step['label']}»)" if step.get("label") and step["label"] != real else real
            out.append({"index": index, "tier": tier, "text": text})
    return out


def describe_step(step: Dict[str, Any], lang: str = "ru") -> str:
    if step.get("label"):
        return step["label"]
    kind = step["type"]
    short = lambda v, n=60: (str(v)[:n] + "…") if len(str(v)) > n else str(v)  # noqa: E731
    if kind == "open":
        what = {"app": tr(lang, "программу", "app"), "url": tr(lang, "ссылку", "link"), "path": tr(lang, "файл", "file"),
                "folder": tr(lang, "папку", "folder"), "settings": tr(lang, "параметры Windows", "Windows Settings")}[step["target"]]
        return tr(lang, "Открыть ", "Open ") + what + (f" «{short(step['value'])}»" if step.get("value") else "")
    if kind == "command":
        catalog = _catalog()
        title = catalog.title(step["action"], lang)
        params = ", ".join(f"{k}: {short(v, 30)}" for k, v in (step.get("params") or {}).items())
        return title + (f" ({params})" if params else "")
    if kind == "ask":
        return tr(lang, "Спросить ИИ: ", "Ask AI: ") + short(step["prompt"])
    if kind == "text":
        verb = {"paste": tr(lang, "Вставить текст", "Paste text"), "copy": tr(lang, "Скопировать текст", "Copy text"),
                "input": tr(lang, "Текст в поле", "Text into the field")}[step["mode"]]
        return f"{verb}: {short(step['text'])}"
    if kind == "system":
        names = {"screenshot": tr(lang, "Скриншот", "Screenshot"), "lock": tr(lang, "Заблокировать компьютер", "Lock the computer"),
                 "dnd": tr(lang, "Не беспокоить", "Do not disturb"), "theme": tr(lang, "Тема Windows", "Windows theme"),
                 "mute": tr(lang, "Звук", "Sound"), "minimize_all": tr(lang, "Свернуть все окна", "Minimize all windows"),
                 "media": tr(lang, "Воспроизведение", "Playback")}
        value = step.get("value") or ""
        values = {"toggle": tr(lang, "переключить", "toggle"), "on": tr(lang, "вкл", "on"), "off": tr(lang, "выкл", "off"),
                  "dark": tr(lang, "тёмная", "dark"), "light": tr(lang, "светлая", "light")}
        return names[step["op"]] + (f": {values.get(value, value)}" if value and step["op"] in ("dnd", "theme", "mute") else "")
    if kind == "dfy":
        return tr(lang, "Сделай за меня: ", "Do it for me: ") + short(step["goal"])
    if kind == "computer":
        return tr(lang, "Управление компьютером: ", "Computer use: ") + short(step["task"])
    if kind == "memuai":
        if step["target"] == "chat":
            return tr(lang, "В чат MemuAI: ", "To a MemuAI chat: ") + short(step.get("text", ""))
        return tr(lang, "Открыть MemuAI: ", "Open MemuAI: ") + step["target"]
    if kind == "wait":
        ms = step["ms"]
        return tr(lang, "Пауза ", "Wait ") + (f"{ms / 1000:g} с" if lang != "en" else f"{ms / 1000:g} s")
    if kind == "keys":
        return tr(lang, "Нажать ", "Press ") + step["keys"]
    if kind == "shell":
        return "PowerShell: " + short(step["command"])
    return kind


# ------------------------------------------------------------------ templates

def render(text: str, ctx: Dict[str, Any], lang: str = "ru") -> str:
    """Fill ``{clipboard}`` & co. Unknown braces stay as they are (JSON in a prompt is fine)."""
    if not isinstance(text, str) or "{" not in text:
        return text
    now = ctx.get("now") or datetime.now()

    def value(name: str) -> str:
        if name == "date":
            return now.strftime("%d.%m.%Y") if lang != "en" else now.strftime("%Y-%m-%d")
        if name == "time":
            return now.strftime("%H:%M")
        if name == "datetime":
            return (now.strftime("%d.%m.%Y %H:%M") if lang != "en" else now.strftime("%Y-%m-%d %H:%M"))
        if name == "weekday":
            return WEEKDAYS["en" if lang == "en" else "ru"][now.weekday()]
        if name == "text":                    # the selection, else the clipboard
            return str(ctx.get("selection") or "") or value("clipboard")
        if name == "clipboard":
            getter = ctx.get("clipboard")
            if callable(getter):
                try:
                    ctx["clipboard"] = str(getter() or "")
                except Exception:
                    ctx["clipboard"] = ""
            return str(ctx.get("clipboard") or "")
        return str(ctx.get(name) or "")

    return PLACEHOLDER_RE.sub(lambda m: value(m.group(1)), text)


def render_params(params: Dict[str, Any], ctx: Dict[str, Any], lang: str) -> Dict[str, Any]:
    return {k: render(v, ctx, lang) if isinstance(v, str) else v for k, v in params.items()}


# ------------------------------------------------------------------ Windows «Do not disturb»

#: ``WNF_SHEL_QUIETHOURS_ACTIVE_PROFILE_CHANGED`` — the Focus assist / Do not disturb profile (0 off, 1 priority
#: only, 2 alarms only). Undocumented but stable since Windows 10 1803; the result is read back to verify.
WNF_QUIET_HOURS = 0x0D83063EA3BF1C75


def _wnf(value: Optional[int] = None) -> Optional[int]:
    if sys.platform != "win32":
        return None
    ntdll = ctypes.WinDLL("ntdll")
    name = ctypes.c_ulonglong(WNF_QUIET_HOURS)
    if value is not None:
        data = ctypes.c_uint32(value)
        status = ntdll.NtUpdateWnfStateData(ctypes.byref(name), ctypes.byref(data), 4, None, None, 0, 0)
        if status != 0:
            return None
    stamp = ctypes.c_ulong(0)
    buffer = ctypes.c_uint32(0)
    size = ctypes.c_ulong(4)
    status = ntdll.NtQueryWnfStateData(ctypes.byref(name), None, None, ctypes.byref(stamp), ctypes.byref(buffer), ctypes.byref(size))
    return int(buffer.value) if status == 0 else None


def set_dnd(mode: str) -> Optional[bool]:
    """Turn Do not disturb on/off/toggle. ``True/False`` = the new state, ``None`` = not possible here."""
    try:
        current = _wnf()
        if current is None:
            return None
        want = (current == 0) if mode == "toggle" else (mode == "on")
        result = _wnf(2 if want else 0)
        return None if result is None or (result != 0) != want else want
    except Exception as exc:                     # noqa: BLE001 - an undocumented API: fall back to Settings
        log.info("quick actions: do not disturb unavailable: %s", exc)
        return None


# ------------------------------------------------------------------ runs

class Run:
    def __init__(self, action: Dict[str, Any], lang: str, ctx: Dict[str, Any]) -> None:
        self.id = "r_" + uuid.uuid4().hex[:10]
        self.action = action
        self.lang = lang
        self.ctx = ctx
        self.status = "running"          # running | confirm | done | error | cancelled
        self.index = -1
        self.steps = [{"title": describe_step(s, lang), "type": s["type"], "status": "pending", "message": ""}
                      for s in action["steps"]]
        self.confirm: Optional[Dict[str, Any]] = None
        self.client: List[Dict[str, Any]] = []
        self.output = ""
        self.error = ""
        self.cards: List[Dict[str, Any]] = []
        self.started = time.time()
        self.finished = 0.0
        self.version = 0
        self.cancelled = asyncio.Event()
        self.answer: Optional[asyncio.Future] = None
        self.client_seq = 0
        self.task: Optional[asyncio.Task] = None

    def touch(self) -> None:
        self.version += 1

    def public(self) -> Dict[str, Any]:
        confirm = None
        if self.confirm:
            confirm = {k: v for k, v in self.confirm.items() if k != "token"}
        return {"id": self.id, "action": self.action["id"], "label": self.action.get("label", ""), "status": self.status,
                "step": self.index, "total": len(self.steps), "steps": [dict(s) for s in self.steps],
                "confirm": confirm, "client": [{k: v for k, v in c.items() if not k.startswith("_")} for c in self.client if not c.get("done")],
                "output": self.output[:20000], "error": self.error, "cards": self.cards[-3:],
                "started": self.started, "finished": self.finished, "version": self.version}


Executor = Callable[..., Awaitable[Dict[str, Any]]]


class Runner:
    """Runs buttons. The executors are injectable (tests pass mocks; production uses the assistant engine)."""

    def __init__(self, *, run_action: Optional[Executor] = None, confirm_token: Optional[Executor] = None,
                 ask_ai: Optional[Callable[[str, str], Awaitable[str]]] = None,
                 insert_text: Optional[Callable[[str], Awaitable[bool]]] = None,
                 show_window: Optional[Callable[[], Awaitable[Any]]] = None,
                 clipboard_get: Optional[Callable[[], str]] = None,
                 dnd: Callable[[str], Optional[bool]] = set_dnd,
                 engine_settings: Optional[Callable[[], Dict[str, Any]]] = None,
                 client_timeout: float = 60.0, ask_timeout: float = 300.0, keep: int = 30) -> None:
        self._run_action = run_action
        self._confirm_token = confirm_token
        self._ask_ai = ask_ai
        self._insert = insert_text
        self._show = show_window
        self._clipboard = clipboard_get
        self._dnd = dnd
        self._engine_settings = engine_settings
        self.client_timeout = client_timeout
        self.ask_timeout = ask_timeout
        self.keep = keep
        self.runs: Dict[str, Run] = {}

    # ---------------------------------------------------------- default executors (the assistant engine)
    def _engine(self):
        from . import assistant_commands
        return assistant_commands.engine()

    async def run_action(self, action_id: str, params: Dict[str, Any], lang: str, confirmed: bool) -> Dict[str, Any]:
        if self._run_action:
            return await self._run_action(action_id, params, lang=lang, confirmed=confirmed)
        return await self._engine().run(action_id, params, source="quick_button", lang=lang, confirmed=confirmed)

    async def confirm_token(self, token: str, accept: bool, lang: str) -> Dict[str, Any]:
        if self._confirm_token:
            return await self._confirm_token(token, accept, lang=lang)
        return await self._engine().confirm(token, accept, lang)

    async def ask_ai(self, prompt: str, lang: str) -> str:
        if self._ask_ai:
            return await self._ask_ai(prompt, lang)
        return await self._engine()._ask_ai(prompt, lang, system=(
            "You are MemuAI Quick Ask running a step of the user's Quick Action button. Do exactly what the request "
            "says and output only the result, without preambles."))

    def engine_settings(self) -> Dict[str, Any]:
        if self._engine_settings:
            return self._engine_settings()
        try:
            return self._engine().settings()
        except Exception:
            return {}

    def _manager(self):
        try:
            from .quick_ask_api import _APP
            app = _APP.get("app")
            return getattr(app.state, "quick_ask", None) if app is not None else None
        except Exception:
            return None

    async def insert_text(self, text: str) -> bool:
        if self._insert:
            return await self._insert(text)
        manager = self._manager()
        if manager is None or not getattr(manager, "hwnd", None):
            return False
        return bool(await asyncio.to_thread(manager.insert_text, text))

    async def show_window(self) -> None:
        if self._show:
            await self._show()
            return
        manager = self._manager()
        if manager is not None and getattr(manager, "hwnd", None) and not getattr(manager, "visible", True):
            await asyncio.to_thread(manager.show, "api")

    def clipboard_get(self) -> str:
        if self._clipboard:
            return self._clipboard()
        try:
            return str(self._engine().backend.clipboard_get() or "")
        except Exception:
            return ""

    # ---------------------------------------------------------- public API
    def start(self, action: Dict[str, Any], *, lang: str = "ru", context: Optional[Dict[str, Any]] = None) -> Run:
        ctx = {"prev": "", "clipboard": self.clipboard_get}
        context = context if isinstance(context, dict) else {}
        for key in ("selection", "input", "page_url", "page_title"):
            ctx[key] = str(context.get(key) or "")[:60000]
        run = Run(action, "en" if lang == "en" else "ru", ctx)
        self.runs[run.id] = run
        while len(self.runs) > self.keep:
            oldest = next(iter(self.runs.values()))
            if oldest.status in ("running", "confirm"):
                break
            self.runs.pop(oldest.id, None)
        run.task = asyncio.ensure_future(self._execute(run))
        return run

    def get(self, run_id: str) -> Optional[Run]:
        return self.runs.get(str(run_id or ""))

    def active(self) -> List[Run]:
        return [r for r in self.runs.values() if r.status in ("running", "confirm")]

    def cancel(self, run_id: str) -> bool:
        run = self.get(run_id)
        if not run or run.status not in ("running", "confirm"):
            return False
        run.cancelled.set()
        if run.answer and not run.answer.done():
            run.answer.set_result({"accept": False, "cancel": True})
        return True

    def answer_confirm(self, run_id: str, accept: bool) -> bool:
        run = self.get(run_id)
        if not run or run.status != "confirm" or not run.answer or run.answer.done():
            return False
        run.answer.set_result({"accept": bool(accept)})
        return True

    def client_done(self, run_id: str, seq: int, ok: bool, result: Any = "", error: str = "") -> bool:
        run = self.get(run_id)
        if not run:
            return False
        for row in run.client:
            if row["seq"] == seq and not row.get("done"):
                row["done"] = True
                future = row.pop("_future", None)
                if future is not None and not future.done():
                    future.set_result({"ok": bool(ok), "result": str(result or "")[:60000], "error": str(error or "")[:400]})
                run.touch()
                return True
        return False

    # ---------------------------------------------------------- execution
    async def _sleep(self, run: Run, seconds: float) -> bool:
        """Wait unless cancelled; ``False`` = cancelled."""
        try:
            await asyncio.wait_for(run.cancelled.wait(), timeout=max(0.0, seconds))
            return False
        except asyncio.TimeoutError:
            return True

    async def _ask_user(self, run: Run, confirm: Dict[str, Any]) -> bool:
        run.status = "confirm"
        run.confirm = confirm
        run.answer = asyncio.get_running_loop().create_future()
        run.touch()
        expires = float(confirm.get("expires") or 0) - time.time() if confirm.get("expires") else 120.0
        try:
            reply = await asyncio.wait_for(run.answer, timeout=max(1.0, expires))
        except asyncio.TimeoutError:
            reply = {"accept": False, "expired": True}
        run.answer = None
        run.confirm = None
        run.status = "running"
        run.touch()
        return bool(reply.get("accept")) and not run.cancelled.is_set()

    async def _client(self, run: Run, payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
        run.client_seq += 1
        future = asyncio.get_running_loop().create_future()
        row = {"seq": run.client_seq, **payload, "_future": future}
        run.client.append(row)
        run.touch()
        cancel = asyncio.ensure_future(run.cancelled.wait())
        try:
            done, _ = await asyncio.wait({future, cancel}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        finally:
            cancel.cancel()
        row["done"] = True
        row.pop("_future", None)
        if future in done:
            return future.result()
        if run.cancelled.is_set():
            return {"ok": False, "error": "cancelled"}
        return {"ok": False, "error": tr(run.lang, "Окно быстрого доступа не ответило", "The Quick Ask window did not respond")}

    async def _execute(self, run: Run) -> None:
        lang = run.lang
        approved = run.action.get("approved") == signature(run.action)
        settings = self.engine_settings()
        try:
            for index, step in enumerate(run.action["steps"]):
                if run.cancelled.is_set():
                    break
                run.index = index
                state = run.steps[index]
                state["status"] = "running"
                run.touch()
                if step.get("confirm"):
                    ok = await self._ask_user(run, {"kind": "step", "message": describe_step(step, lang), "tier": "confirm",
                                                    "countdown": 0, "expires": time.time() + 120})
                    if not ok:
                        state["status"] = "cancelled"
                        state["message"] = tr(lang, "Отменено", "Cancelled")
                        run.cancelled.set()
                        break
                try:
                    message, output = await self._step(run, step, approved, settings)
                    state["status"] = "done"
                    state["message"] = message[:400]
                    if output is not None:
                        run.ctx["prev"] = output
                        run.output = output
                except StepFailed as exc:
                    state["status"] = "cancelled" if exc.cancelled else "error"
                    state["message"] = exc.message[:400]
                    if exc.cancelled:
                        run.cancelled.set()
                        break
                    if not step.get("continue_on_error"):
                        run.error = exc.message[:400]
                        break
                run.touch()
            if run.cancelled.is_set():
                run.status = "cancelled"
                for state in run.steps:
                    if state["status"] in ("pending", "running"):
                        state["status"] = "skipped" if state["status"] == "pending" else "cancelled"
            elif run.error:
                run.status = "error"
                for state in run.steps:
                    if state["status"] == "pending":
                        state["status"] = "skipped"
            else:
                run.status = "done"
        except Exception as exc:                 # noqa: BLE001 - a broken step never leaves the run hanging
            log.exception("quick action run failed")
            run.status = "error"
            run.error = f"{type(exc).__name__}: {exc}"[:400]
        finally:
            run.finished = time.time()
            run.confirm = None
            run.touch()

    async def _step(self, run: Run, step: Dict[str, Any], approved: bool, settings: Dict[str, Any]) -> Tuple[str, Optional[str]]:
        """Run one step: (message, output for ``{prev}`` or None). Raises :class:`StepFailed`."""
        lang, kind, ctx = run.lang, step["type"], run.ctx
        if kind == "wait":
            if not await self._sleep(run, step["ms"] / 1000.0):
                raise StepFailed(tr(lang, "Отменено", "Cancelled"), cancelled=True)
            return "", None
        if kind == "ask":
            prompt = render(step["prompt"], ctx, lang)
            if step.get("mode") == "silent":
                # «Остановить» interrupts the model call itself (it used to wait for the whole answer, up to minutes)
                job = asyncio.ensure_future(self.ask_ai(prompt, lang))
                stop = asyncio.ensure_future(run.cancelled.wait())
                try:
                    await asyncio.wait({job, stop}, timeout=self.ask_timeout, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    stop.cancel()
                if not job.done():
                    job.cancel()
                    if run.cancelled.is_set():
                        raise StepFailed(tr(lang, "Отменено", "Cancelled"), cancelled=True)
                    raise StepFailed(tr(lang, "Модель не ответила вовремя", "The model did not answer in time"))
                try:
                    text = job.result()
                except Exception as exc:          # noqa: BLE001 - ActionError / timeouts / network
                    raise StepFailed(_error_text(exc, lang)) from None
                return tr(lang, "Ответ получен", "Answer received"), str(text or "")
            await self.show_window()
            reply = await self._client(run, {"type": "ask", "text": prompt}, self.ask_timeout)
            if not reply.get("ok"):
                raise StepFailed(reply.get("error") or tr(lang, "Не удалось спросить", "Could not ask"),
                                 cancelled=reply.get("error") == "cancelled")
            return tr(lang, "Ответ в окне", "Answer in the window"), reply.get("result") or ""
        if kind == "text" and step["mode"] in ("paste", "input"):
            text = render(step["text"], ctx, lang)
            if step["mode"] == "input":
                await self.show_window()
                reply = await self._client(run, {"type": "input", "text": text}, self.client_timeout)
                if not reply.get("ok"):
                    raise StepFailed(reply.get("error") or "", cancelled=reply.get("error") == "cancelled")
                return tr(lang, "Текст в поле", "Text in the field"), text
            if not await self.insert_text(text):
                raise StepFailed(tr(lang, "Вставка в программу работает в приложении MemuAI для Windows",
                                    "Pasting into a program works in the MemuAI Windows app"))
            return tr(lang, "Вставлено", "Pasted"), text
        if kind == "system" and step["op"] == "dnd":
            state = await asyncio.to_thread(self._dnd, step.get("value") or "toggle")
            if state is None:
                card = await self.run_action("open.settings", {"page": "focus"}, lang, False)
                if card.get("status") != "done":
                    raise StepFailed(card.get("message") or tr(lang, "«Не беспокоить» недоступно", "Do not disturb is unavailable"))
                return tr(lang, "Открыл настройки «Не беспокоить» — переключите режим там", "Opened Do not disturb settings — switch it there"), None
            return (tr(lang, "«Не беспокоить» включено", "Do not disturb is on") if state
                    else tr(lang, "«Не беспокоить» выключено", "Do not disturb is off")), None
        call = engine_call(step)
        if call is None:
            raise StepFailed(tr(lang, "Шаг не поддерживается", "Unsupported step"))
        action_id, params = call
        rendered = render_params(params, ctx, lang)
        tier = step_tier(step, settings, rendered)
        # approved «confirm» steps run without a second question — unless a template filled the parameters;
        # «dangerous» ones always get the engine's confirmation card.
        confirmed = approved and tier == "confirm" and not _has_template(params)
        card = await self.run_action(action_id, rendered, lang, confirmed)
        if card.get("status") == "confirm" and card.get("confirm"):
            info = card["confirm"]
            accepted = await self._ask_user(run, {"kind": "engine", "message": card.get("message") or describe_step(step, lang),
                                                  "tier": info.get("tier") or tier, "countdown": int(info.get("countdown") or 0),
                                                  "expires": float(info.get("expires") or time.time() + 120), "token": info.get("token")})
            card = await self.confirm_token(str(info.get("token") or ""), accepted, lang)
            if not accepted:
                raise StepFailed(tr(lang, "Отменено", "Cancelled"), cancelled=True)
        run.cards.append(_card_summary(card))
        status = card.get("status")
        if card.get("client") and status in ("done", "running"):
            reply = await self._client(run, {"type": "card", "card": _card_summary(card, full=True)}, self.client_timeout)
            if not reply.get("ok"):
                raise StepFailed(reply.get("error") or tr(lang, "Окно не приняло задачу", "The window did not take the task"),
                                 cancelled=reply.get("error") == "cancelled")
            return card.get("message") or "", None
        if status not in ("done", "running"):
            raise StepFailed(str(card.get("message") or card.get("title") or status), cancelled=status == "cancelled")
        out = card.get("copy") or card.get("answer") or card.get("message") or ""
        return str(card.get("message") or ""), str(out) if out else None


class StepFailed(Exception):
    def __init__(self, message: str, *, cancelled: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.cancelled = cancelled


def _error_text(exc: Exception, lang: str) -> str:
    text = getattr(exc, "text", None)
    if callable(text):
        try:
            return str(text(lang))
        except Exception:
            pass
    if isinstance(exc, asyncio.TimeoutError):
        return tr(lang, "Модель не ответила вовремя", "The model did not answer in time")
    return str(exc) or type(exc).__name__


def _card_summary(card: Dict[str, Any], full: bool = False) -> Dict[str, Any]:
    keys = ("id", "action", "title", "status", "message", "kind", "undo", "client") if full else \
        ("id", "action", "title", "status", "message", "undo")
    return {k: card[k] for k in keys if k in card}


# ------------------------------------------------------------------ ready-made buttons

def templates(lang: str = "ru") -> List[Dict[str, Any]]:
    """Ready-made buttons (gallery «Шаблоны»). Every one passes :func:`validate_action`."""
    t = lambda ru, en: tr(lang, ru, en)  # noqa: E731
    rows = [
        {"key": "morning", "label": t("Утренний старт", "Morning start"), "icon": {"kind": "emoji", "value": "☀️"},
         "color": "#f59e0b", "color2": "#ef4444",
         "tooltip": t("Почта, календарь и сводка дня от ИИ", "Mail, calendar and an AI brief of the day"),
         "steps": [{"type": "open", "target": "url", "value": "https://mail.google.com"},
                   {"type": "open", "target": "url", "value": "https://calendar.google.com"},
                   {"type": "ask", "mode": "window", "prompt": t(
                       "Сегодня {weekday}, {date}. Составь короткий план продуктивного дня: 3 главных дела и одна идея для перерыва.",
                       "Today is {weekday}, {date}. Make a short plan for a productive day: 3 key tasks and one idea for a break.")}]},
        {"key": "call", "label": t("Созвон", "Call"), "icon": {"kind": "builtin", "value": "video"},
         "color": "#3b82f6", "color2": "#8b5cf6",
         "tooltip": t("Громкость 30 %, «Не беспокоить», календарь", "Volume 30%, Do not disturb, calendar"),
         "steps": [{"type": "command", "action": "volume.set", "params": {"level": 30}},
                   {"type": "system", "op": "dnd", "value": "on", "continue_on_error": True},
                   {"type": "open", "target": "url", "value": "https://calendar.google.com"}]},
        {"key": "translate", "label": t("Перевести буфер", "Translate clipboard"), "icon": {"kind": "builtin", "value": "translate"},
         "color": "#10b981", "color2": "#06b6d4",
         "steps": [{"type": "ask", "mode": "window", "prompt": t(
             "Переведи на {target_lang} и выдай только перевод:\n\n{clipboard}", "Translate into {target_lang}, output only the translation:\n\n{clipboard}"
         ).replace("{target_lang}", t("русский (если текст уже русский — на английский)", "English (if it is already English — into Russian)"))}]},
        {"key": "fix", "label": t("Исправить текст", "Fix the text"), "icon": {"kind": "builtin", "value": "spell"},
         "color": "#a855f7", "color2": "",
         "tooltip": t("Исправит ошибки в выделенном (или скопированном) тексте и вставит обратно",
                      "Fixes the selected (or copied) text and pastes it back"),
         "steps": [{"type": "ask", "mode": "silent", "prompt": t(
             "Исправь орфографию и пунктуацию, сохрани стиль. Выдай только исправленный текст:\n\n{text}",
             "Fix spelling and punctuation, keep the style. Output only the fixed text:\n\n{text}")},
             {"type": "text", "mode": "paste", "text": "{prev}"}]},
        {"key": "focus", "label": t("Фокус 25 мин", "Focus 25 min"), "icon": {"kind": "builtin", "value": "timer"},
         "color": "#ef4444", "color2": "#f97316",
         "steps": [{"type": "system", "op": "dnd", "value": "on", "continue_on_error": True},
                   {"type": "command", "action": "pomodoro.control", "params": {"action": "start", "work": 25}}]},
        {"key": "screenshot", "label": t("Скриншот", "Screenshot"), "icon": {"kind": "builtin", "value": "camera"},
         "color": "#64748b", "color2": "", "steps": [{"type": "system", "op": "screenshot", "value": "region"}]},
        {"key": "lock", "label": t("Заблокировать", "Lock"), "icon": {"kind": "builtin", "value": "lock"},
         "color": "#334155", "color2": "", "steps": [{"type": "system", "op": "lock"}]},
        {"key": "dark", "label": t("Тема Windows", "Windows theme"), "icon": {"kind": "builtin", "value": "moon"},
         "color": "#1e293b", "color2": "#6366f1", "steps": [{"type": "system", "op": "theme", "value": "toggle"}]},
        {"key": "downloads", "label": t("Загрузки", "Downloads"), "icon": {"kind": "builtin", "value": "folder"},
         "color": "#0ea5e9", "color2": "", "steps": [{"type": "open", "target": "folder", "value": "downloads"}]},
        {"key": "music", "label": t("Пауза/музыка", "Play/pause"), "icon": {"kind": "builtin", "value": "music"},
         "color": "#ec4899", "color2": "#f43f5e", "steps": [{"type": "system", "op": "media", "value": "play_pause"}]},
        {"key": "side", "label": t("Окна рядом", "Side by side"), "icon": {"kind": "builtin", "value": "columns"},
         "color": "#14b8a6", "color2": "", "steps": [{"type": "command", "action": "window.arrange", "params": {"layout": "side_by_side"}}]},
        {"key": "chat", "label": t("Новый чат MemuAI", "New MemuAI chat"), "icon": {"kind": "builtin", "value": "chat"},
         "color": "", "color2": "", "steps": [{"type": "memuai", "target": "new_chat"}]},
    ]
    out = []
    for row in rows:
        key = row.pop("key")
        try:
            clean = validate_action({**row, "id": ""}, keep_meta=False)
        except ActionInvalid as exc:          # a template that stopped matching the catalogue is skipped, not fatal
            log.warning("quick actions: template %s is invalid: %s", key, exc)
            continue
        clean.pop("id", None)
        clean.pop("created", None)
        clean.pop("updated", None)
        clean.pop("approved", None)
        out.append({"key": key, **clean})
    return out


def step_catalog(lang: str = "ru") -> Dict[str, Any]:
    """What the editor offers: the assistant commands (public catalogue, Windows-only marked) and the enums."""
    catalog = _catalog()
    commands = [row for row in catalog.public(lang) if not row["id"].startswith("_")]
    return {"commands": commands, "categories": catalog.categories(lang), "folders": list(catalog.FOLDERS),
            "settings_pages": [p for p in catalog.SETTINGS_PAGES if p], "memuai_targets": list(catalog.MEMUAI_TARGETS),
            "placeholders": list(PLACEHOLDERS), "step_types": list(STEP_TYPES), "system_ops": list(SYSTEM_OPS),
            "reserved_hotkeys": sorted(RESERVED_HOTKEYS), "platform": sys.platform}
