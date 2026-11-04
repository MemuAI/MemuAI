# -*- coding: utf-8 -*-
"""Программные сценарии планирования: шаги, переменные, файлы, запуск и макросы.

Сценарий — это список шагов, который хранится в задаче планирования
(``kind == "scenario"``, поле ``scenario``) и выполняется планировщиком
без модели (кроме явного шага «Спросить модель»): быстро, предсказуемо и
с полным журналом. Группы шагов:

* данные — ``set`` (присвоить, прибавить, переключить, удалить; область
  ``run`` / ``task`` / ``global``: переменные задачи и общие переменные
  сохраняются между запусками), ``calc`` (безопасное выражение без
  ``eval``), ``text``, ``random``, ``date``, ``json``, ``list``;
* файлы (только внутри базы) — ``file_create``, ``file_edit``,
  ``file_read``, ``file_info``, ``file_list``, ``file_copy``,
  ``file_move``, ``file_delete``, ``folder_create``, ``csv_append``,
  ``zip``;
* логика — ``if``, ``repeat``, ``foreach``, ``try``, ``wait``, ``stop``,
  ``log``, ``macro`` (группа шагов) и ``macro_call`` (макрос из
  библиотеки, :mod:`app.plan_vars`);
* система — ``run`` (белый список, argv без shell), ``system`` (CPU, RAM,
  диск, батарея, процесс), ``open_url``;
* сервисы — ``integration`` (Telegram Use / Chrome), ``http`` (только
  публичные адреса, защита от SSRF), ``telegram``, ``notify`` (строка
  результата), ``alert`` (Windows / Входящие), ``ai`` (запрос к модели),
  ``web_search``, ``memubase``, ``memory``, ``task`` (запустить /
  включить / выключить другую задачу), ``module`` (действие модуля),
  ``command`` (команда быстрого доступа), ``assistant`` (действие
  ассистента).

Подстановка ``${var}`` работает в текстах, путях и аргументах
(``${var:-по умолчанию}``, ``${list.0}``, ``${global.name}``). База
файлов — каталог данных пользователя (или папка, явно выбранная им в
задаче): путь за её пределы через ``..`` или симлинк отклоняется. Шаг,
который пишет файлы, запускает программы или шлёт данные наружу
(``DESTRUCTIVE``), выполняется только если пользователь подтвердил
сценарий при сохранении (``confirmed``). Пробный прогон (``dry``)
выполняет только читающие шаги, остальные описывает. Всё ограничено:
не больше 150 шагов в сценарии, 400 выполненных шагов, глубина 5,
повтор до 20, перебор до 200 элементов, файл до 1 МБ, вывод команды до
64 КБ, таймаут команды до 300 с, ожидание до 300 с.
"""
from __future__ import annotations

import ast
import asyncio
import base64
import csv
import fnmatch
import hashlib
import io
import json
import math
import operator
import os
import random
import re
import secrets
import shutil
import string
import time
import uuid
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote, unquote

MAX_STEPS = 150
MAX_EXECUTED = 400
MAX_DEPTH = 5
MAX_REPEAT = 20
MAX_FOREACH = 200
MAX_VARIABLES = 80
MAX_FILE_CHARS = 1_000_000
MAX_OUTPUT_CHARS = 64_000
MAX_TEXT = 20_000
MAX_VALUE_CHARS = 200_000
MAX_LIST_ITEMS = 2_000
MAX_WAIT = 300
MAX_ZIP_FILES = 2_000
MAX_ZIP_BYTES = 200 * 1024 * 1024
RUN_TIMEOUT_DEFAULT = 30
RUN_TIMEOUT_MAX = 300

STEP_TYPES = (
    # данные
    "set", "calc", "text", "random", "date", "json", "list",
    # файлы
    "file_create", "file_edit", "file_read", "file_info", "file_list", "file_copy", "file_move",
    "file_delete", "folder_create", "csv_append", "zip",
    # логика
    "if", "repeat", "foreach", "try", "wait", "stop", "log", "macro", "macro_call",
    # система
    "run", "system", "open_url",
    # сервисы
    "integration", "http", "telegram", "notify", "alert", "ai", "web_search", "memubase", "memory",
    "task", "module", "command", "assistant",
)
# Шаги, которые меняют файлы, запускают программы или отправляют данные наружу.
DESTRUCTIVE = ("file_create", "file_edit", "run", "file_copy", "file_move", "file_delete",
               "folder_create", "csv_append", "zip")
# Шаги с внешним эффектом: в пробном прогоне они только описываются.
SIDE_EFFECT = DESTRUCTIVE + ("integration", "telegram", "alert", "ai", "web_search", "memory", "task",
                             "module", "command", "assistant", "open_url", "wait")
CONTAINERS = {"if": ("then", "else"), "repeat": ("steps",), "foreach": ("steps",), "try": ("steps", "catch"),
              "macro": ("steps",)}
COND_OPS = ("eq", "ne", "gt", "ge", "lt", "le", "contains", "not_contains", "empty", "not_empty",
            "starts_with", "ends_with", "regex", "is_number", "is_true", "is_false", "exists", "in_list")
SCOPES = ("run", "task", "global")
SET_OPS = ("set", "inc", "dec", "add", "mul", "append", "toggle", "delete")
TEXT_OPS = ("upper", "lower", "title", "trim", "replace", "regex_replace", "regex_find", "regex_all", "slice",
            "length", "split", "lines", "words", "first_line", "last_line", "contains", "starts_with",
            "ends_with", "pad", "url_encode", "url_decode", "base64_encode", "base64_decode", "sha256", "md5",
            "number", "template")
LIST_OPS = ("push", "pop", "shift", "unshift", "clear", "length", "get", "remove", "contains", "unique",
            "sort", "reverse", "join", "slice", "sum", "min", "max", "from_text")
JSON_OPS = ("get", "set", "parse", "stringify", "keys", "length", "merge", "delete")
DATE_MODES = ("now", "today", "timestamp", "weekday", "format", "add", "diff_days", "diff_minutes",
              "start_of_week", "start_of_month", "parse")
RANDOM_MODES = ("int", "float", "pick", "shuffle", "uuid", "password", "bool")
SYSTEM_METRICS = ("cpu_pct", "ram_pct", "ram_free_gb", "disk_free_gb", "disk_free_pct", "battery_pct",
                  "plugged", "uptime_hours", "hostname", "os", "user", "process", "processes", "time")
HTTP_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE")
READONLY_HTTP = ("GET", "HEAD")

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}$")
_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_.]*)(?::-([^}]*))?\}")
_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")


class ScenarioError(ValueError):
    """Ошибка сценария с русским и английским текстом для пользователя."""

    def __init__(self, ru: str, en: str) -> None:
        super().__init__(ru)
        self.ru, self.en = ru, en

    def text(self, lang: str) -> str:
        return self.en if lang == "en" else self.ru


class _Stop(Exception):
    """Шаг «Завершить»: досрочный конец сценария."""

    def __init__(self, ok: bool, text: str) -> None:
        super().__init__(text)
        self.ok, self.text = ok, text


def _err(ru: str, en: str) -> ScenarioError:
    return ScenarioError(ru, en)


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return max(lo, min(hi, int(round(number))))


def _float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    return str(value if value is not None else "")[:limit]


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "да")
    return default if value is None else bool(value)


def _enum(value: Any, options: Iterable[str], default: str) -> str:
    text = str(value or "").strip()
    opts = tuple(options)
    if text in opts:
        return text
    if text.lower() in opts:
        return text.lower()
    if text.upper() in opts:
        return text.upper()
    if text:
        raise _err(f"Неизвестное значение «{text[:30]}»", f"Unknown value “{text[:30]}”")
    return default


def _resolve_path(base: Path, rel: Any, *, allow_root: bool = False) -> Path:
    """Путь внутри базы: ``..`` и симлинки наружу отклоняются."""
    raw = str(rel or "").strip().replace("\\", "/")
    if allow_root and raw in ("", ".", "./"):
        return base.resolve()
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise _err("Путь к файлу должен быть относительным, внутри выбранной папки",
                   "The file path must be relative, inside the chosen folder")
    root = base.resolve()
    target = (root / raw).resolve()
    if target != root and root not in target.parents:
        raise _err("Путь выходит за пределы выбранной папки", "The path leaves the chosen folder")
    if target == root and not allow_root:
        raise _err("Укажите файл внутри папки, а не саму папку", "Name a file inside the folder, not the folder itself")
    return target


def resolve_var(name: str, variables: Dict[str, Any]) -> Any:
    """``loop.index`` / ``last.ok`` / ``items.0`` — точечный путь по переменным."""
    value: Any = variables
    for part in name.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and re.fullmatch(r"-?\d{1,6}", part):
            index = int(part)
            if -len(value) <= index < len(value):
                value = value[index]
            else:
                return None
        else:
            return None
    return value


def _plain(value: Any, limit: int = 2000) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)[:limit]
        except (TypeError, ValueError):
            return str(value)[:limit]
    return str(value)


def substitute(text: Any, variables: Dict[str, Any]) -> str:
    """``${var}`` и ``${var:-по умолчанию}``; неизвестная переменная — пусто."""
    def repl(match: "re.Match[str]") -> str:
        value = resolve_var(match.group(1), variables)
        if value is None:
            return match.group(2) or ""
        return _plain(value, 5000)
    return _VAR_RE.sub(repl, _text(text, MAX_TEXT * 5))


def raw_value(text: Any, variables: Dict[str, Any]) -> Any:
    """Шаблон ровно из одной ``${var}`` возвращает само значение (список остаётся списком)."""
    raw = str(text if text is not None else "").strip()
    match = _VAR_RE.fullmatch(raw)
    if match:
        value = resolve_var(match.group(1), variables)
        return match.group(2) or "" if value is None else value
    if _NAME_RE.match(raw) and raw in variables and isinstance(variables[raw], (list, dict)):
        return variables[raw]
    return substitute(raw, variables)


def _coerce(value: Any) -> Any:
    """Текст «42» становится числом, «true» — логическим значением, JSON-список — списком."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text == "":
        return value
    if text in ("true", "false"):
        return text == "true"
    if re.fullmatch(r"[-+]?\d{1,15}", text):
        return int(text)
    if re.fullmatch(r"[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?", text):
        number = _float(text, math.nan)
        if math.isfinite(number):
            return number
    if text[:1] in "[{" and text[-1:] in "]}" and len(text) <= MAX_VALUE_CHARS:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, (list, dict)):
                return parsed
        except ValueError:
            pass
    return value


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _tidy_number(number: float) -> Any:
    if isinstance(number, bool):
        return number
    if isinstance(number, int):
        return number
    if math.isfinite(number) and number == int(number) and abs(number) < 1e15:
        return int(number)
    return round(number, 10)


def _check_value(value: Any) -> Any:
    """Значения переменных ограничены по размеру (сохраняются между запусками)."""
    if isinstance(value, str) and len(value) > MAX_VALUE_CHARS:
        return value[:MAX_VALUE_CHARS]
    if isinstance(value, list) and len(value) > MAX_LIST_ITEMS:
        value = value[-MAX_LIST_ITEMS:]
    if isinstance(value, (list, dict)):
        try:
            encoded = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            raise _err("Значение нельзя сохранить в переменную", "The value cannot be stored in a variable")
        if len(encoded) > MAX_VALUE_CHARS:
            raise _err("Значение переменной слишком большое (больше 200 000 символов)",
                       "The variable value is too large (over 200,000 characters)")
    return value


# --------------------------------------------------------------- безопасные выражения
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_CMP = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
        ast.Gt: operator.gt, ast.GtE: operator.ge}


def _fn_round(x: Any, n: Any = 0) -> Any:
    return round(float(x), int(n))


_FUNCS: Dict[str, Callable[..., Any]] = {
    "abs": abs, "min": min, "max": max, "round": _fn_round, "int": lambda x: int(float(x)), "float": float,
    "str": _plain, "len": lambda x: len(x if isinstance(x, (list, dict, str)) else str(x)),
    "floor": lambda x: math.floor(float(x)), "ceil": lambda x: math.ceil(float(x)),
    "sqrt": lambda x: math.sqrt(float(x)), "upper": lambda x: str(x).upper(), "lower": lambda x: str(x).lower(),
    "sum": lambda x: sum(float(v) for v in x), "avg": lambda x: (sum(float(v) for v in x) / len(x)) if x else 0,
    "pct": lambda part, whole: (float(part) / float(whole) * 100) if float(whole) else 0,
    "clamp": lambda x, lo, hi: max(float(lo), min(float(hi), float(x))),
    "bool": lambda x: bool(_coerce(x)) if isinstance(x, str) else bool(x),
}


def calc(expr: str, variables: Dict[str, Any]) -> Any:
    """Вычислить выражение: числа, строки, + − × ÷ // % **, сравнения, and/or/not, ``a if c else b``,
    переменные (``count``, ``loop.index``, ``items[0]``) и функции из белого списка. Без ``eval``."""
    source = str(expr or "").strip()
    if not source:
        raise _err("Пустое выражение", "An empty expression")
    if len(source) > 500:
        raise _err("Выражение длиннее 500 символов", "The expression is longer than 500 characters")
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError:
        raise _err(f"Ошибка в выражении «{source[:60]}»", f"An error in the expression “{source[:60]}”") from None
    budget = [0]

    def ev(node: ast.AST) -> Any:
        budget[0] += 1
        if budget[0] > 400:
            raise _err("Выражение слишком сложное", "The expression is too complex")
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, str, bool)) or \
                isinstance(node, ast.Constant) and node.value is None:
            return node.value
        if isinstance(node, ast.Name):
            low = node.id.lower()
            if low in ("true", "false"):
                return low == "true"
            if low in ("none", "null"):
                return None
            if node.id in ("pi",):
                return math.pi
            value = variables.get(node.id)
            return _coerce(value) if isinstance(value, str) else value
        if isinstance(node, ast.Attribute):
            base = ev(node.value)
            if isinstance(base, dict):
                return base.get(node.attr)
            return None
        if isinstance(node, ast.Subscript):
            base = ev(node.value)
            key = ev(node.slice)
            if isinstance(base, list) and isinstance(key, (int, float)) and not isinstance(key, bool):
                index = int(key)
                return base[index] if -len(base) <= index < len(base) else None
            if isinstance(base, dict):
                return base.get(str(key))
            if isinstance(base, str) and isinstance(key, (int, float)):
                index = int(key)
                return base[index] if -len(base) <= index < len(base) else ""
            return None
        if isinstance(node, ast.List):
            return [ev(item) for item in node.elts[:200]]
        if isinstance(node, ast.UnaryOp):
            value = ev(node.operand)
            if isinstance(node.op, ast.Not):
                return not value
            number = _num(value)
            if number is None:
                raise _err("Минус применим только к числу", "Minus applies to numbers only")
            return -number if isinstance(node.op, ast.USub) else number
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                result: Any = True
                for item in node.values:
                    result = ev(item)
                    if not result:
                        return result
                return result
            result = False
            for item in node.values:
                result = ev(item)
                if result:
                    return result
            return result
        if isinstance(node, ast.IfExp):
            return ev(node.body) if ev(node.test) else ev(node.orelse)
        if isinstance(node, ast.Compare):
            left = ev(node.left)
            for op, comp in zip(node.ops, node.comparators):
                right = ev(comp)
                fn = _CMP.get(type(op))
                if fn is None:
                    if isinstance(op, (ast.In, ast.NotIn)):
                        hay = right if isinstance(right, (list, dict, str)) else _plain(right)
                        inside = (_plain(left) in hay) if isinstance(hay, str) else (left in hay)
                        ok = inside if isinstance(op, ast.In) else not inside
                    else:
                        raise _err("Неподдерживаемое сравнение", "Unsupported comparison")
                else:
                    ln, rn = _num(left), _num(right)
                    try:
                        ok = fn(ln, rn) if ln is not None and rn is not None else fn(_plain(left), _plain(right))
                    except TypeError:
                        ok = False
                if not ok:
                    return False
                left = right
            return True
        if isinstance(node, ast.BinOp):
            fn = _BIN.get(type(node.op))
            if fn is None:
                raise _err("Неподдерживаемая операция", "Unsupported operation")
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Add) and (isinstance(left, str) and _num(left) is None
                                                 or isinstance(right, str) and _num(right) is None):
                text = _plain(left) + _plain(right)
                if len(text) > MAX_TEXT:
                    raise _err("Строка слишком длинная", "The string is too long")
                return text
            if isinstance(node.op, ast.Add) and isinstance(left, list) and isinstance(right, list):
                return (left + right)[:MAX_LIST_ITEMS]
            ln, rn = _num(left), _num(right)
            if ln is None or rn is None:
                raise _err("В арифметике нужны числа", "Arithmetic needs numbers")
            if isinstance(node.op, ast.Pow) and (abs(rn) > 64 or abs(ln) > 1e6):
                raise _err("Слишком большая степень", "The power is too large")
            if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and rn == 0:
                raise _err("Деление на ноль", "Division by zero")
            value = fn(ln, rn)
            if isinstance(value, complex) or not math.isfinite(value) or abs(value) > 1e18:
                raise _err("Результат вне допустимого диапазона", "The result is out of range")
            return value
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS or node.keywords:
                name = getattr(node.func, "id", "?")
                raise _err(f"Функция «{name}» недоступна", f"Function “{name}” is not available")
            args = [ev(a) for a in node.args[:8]]
            if node.func.id in ("min", "max") and len(args) == 1 and isinstance(args[0], list):
                args = [_num(v) if _num(v) is not None else v for v in args[0]] or [0]
            elif node.func.id in ("min", "max"):
                args = [_num(v) if _num(v) is not None else v for v in args]
            try:
                return _FUNCS[node.func.id](*args)
            except (TypeError, ValueError, ZeroDivisionError, OverflowError):
                raise _err(f"Ошибка в функции {node.func.id}()", f"An error in {node.func.id}()") from None
        raise _err("В выражении есть недопустимая конструкция", "The expression contains a forbidden construct")

    value = ev(tree)
    if isinstance(value, float):
        return _tidy_number(value)
    return value


# --------------------------------------------------------------- проверка шагов
def _normalize_cond(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise _err("Условие должно быть объектом", "The condition must be an object")
    left = str(raw.get("left") or raw.get("var") or raw.get("name") or "")
    op = str(raw.get("op") or "eq").lower()
    if op not in COND_OPS:
        raise _err(f"Неизвестное сравнение «{op[:20]}»", f"Unknown comparison “{op[:20]}”")
    return {"left": _text(left, 500), "op": op,
            "right": _text(raw.get("right") if "right" in raw else raw.get("value"), 500)}


def _var_name(raw: Dict[str, Any], *keys: str, required: bool = True, default: str = "") -> str:
    name = ""
    for key in keys:
        if str(raw.get(key) or "").strip():
            name = str(raw.get(key)).strip()
            break
    name = name or default
    if not name and not required:
        return ""
    if not _NAME_RE.match(name):
        raise _err("Имя переменной — латиница, цифры и _ (например, count)",
                   "A variable name uses Latin letters, digits and _ (for example count)")
    return name


def _need(step: Dict[str, Any], key: str, ru: str, en: str) -> None:
    if not str(step.get(key) or "").strip():
        raise _err(ru, en)


def _scope(raw: Dict[str, Any]) -> str:
    scope = str(raw.get("scope") or "run").lower()
    if scope in ("persist", "persistent", "saved"):
        scope = "task"
    return scope if scope in SCOPES else "run"


def _normalize_step(raw: Any, depth: int, counter: List[int]) -> Dict[str, Any]:
    if depth > MAX_DEPTH:
        raise _err("Сценарий вложен слишком глубоко", "The scenario is nested too deeply")
    if not isinstance(raw, dict):
        raise _err("Шаг сценария должен быть объектом", "A scenario step must be an object")
    counter[0] += 1
    if counter[0] > MAX_STEPS:
        raise _err(f"Слишком много шагов (больше {MAX_STEPS})", f"Too many steps (over {MAX_STEPS})")
    kind = str(raw.get("type") or "").strip().lower()
    if kind not in STEP_TYPES:
        raise _err(f"Неизвестный шаг «{kind[:30]}»", f"Unknown step “{kind[:30]}”")
    step: Dict[str, Any] = {"type": kind}
    label = _text(raw.get("label"), 120)
    if label:
        step["label"] = label
    if _bool(raw.get("disabled")):
        step["disabled"] = True
    if _bool(raw.get("continue_on_error")):
        step["continue_on_error"] = True
    sub = lambda key: [_normalize_step(s, depth + 1, counter) for s in (raw.get(key) or [])[:MAX_STEPS]]  # noqa: E731

    if kind == "set":
        step["name"] = _var_name(raw, "name", "var")
        op = str(raw.get("op") or "set").lower()
        if op not in SET_OPS:
            raise _err(f"Неизвестная операция «{op[:20]}»", f"Unknown operation “{op[:20]}”")
        step["op"] = op
        if op in ("set", "append"):
            step["value"] = _text(raw.get("value"), MAX_TEXT)
        elif op in ("inc", "dec", "add", "mul"):
            by = _float(raw.get("by"), 1.0 if op != "mul" else 2.0)
            step["by"] = _tidy_number(max(-1e12, min(1e12, by)))
        scope = _scope(raw)
        if scope != "run":
            step["scope"] = scope
    elif kind == "calc":
        step["name"] = _var_name(raw, "name", "var")
        step["expr"] = _text(raw.get("expr") or raw.get("expression") or raw.get("value"), 500)
        _need(step, "expr", "Введите выражение, например count * 2 + 1", "Enter an expression, e.g. count * 2 + 1")
        if raw.get("decimals") not in (None, ""):
            step["decimals"] = _int(raw.get("decimals"), 2, 0, 10)
        scope = _scope(raw)
        if scope != "run":
            step["scope"] = scope
    elif kind == "text":
        step["name"] = _var_name(raw, "name", "var")
        step["op"] = _enum(raw.get("op"), TEXT_OPS, "trim")
        step["input"] = _text(raw.get("input") if raw.get("input") is not None else raw.get("value"), MAX_TEXT)
        step["arg1"] = _text(raw.get("arg1") if raw.get("arg1") is not None else raw.get("find"), 2000)
        step["arg2"] = _text(raw.get("arg2") if raw.get("arg2") is not None else raw.get("replace"), 2000)
        if step["op"] in ("regex_replace", "regex_find", "regex_all") and step["arg1"]:
            try:
                re.compile(step["arg1"])
            except re.error:
                raise _err("Регулярное выражение с ошибкой", "The regular expression is invalid") from None
            if len(step["arg1"]) > 300:
                raise _err("Регулярное выражение длиннее 300 символов", "The regular expression is longer than 300 characters")
        scope = _scope(raw)
        if scope != "run":
            step["scope"] = scope
    elif kind == "random":
        step["name"] = _var_name(raw, "name", "var")
        step["mode"] = _enum(raw.get("mode"), RANDOM_MODES, "int")
        step["min"] = _tidy_number(_float(raw.get("min"), 1))
        step["max"] = _tidy_number(_float(raw.get("max"), 100))
        step["items"] = _text(raw.get("items"), MAX_TEXT)
        step["length"] = _int(raw.get("length"), 12, 4, 64)
        if step["mode"] in ("pick", "shuffle") and not step["items"].strip():
            raise _err("Перечислите варианты — по одному в строке или через запятую",
                       "List the options — one per line or comma-separated")
        scope = _scope(raw)
        if scope != "run":
            step["scope"] = scope
    elif kind == "date":
        step["name"] = _var_name(raw, "name", "var")
        step["mode"] = _enum(raw.get("mode"), DATE_MODES, "now")
        step["value"] = _text(raw.get("value"), 200)
        step["value2"] = _text(raw.get("value2"), 200)
        step["format"] = _text(raw.get("format"), 80)
        step["amount"] = _tidy_number(_float(raw.get("amount"), 1))
        step["unit"] = _enum(raw.get("unit"), ("minutes", "hours", "days", "weeks"), "days")
        scope = _scope(raw)
        if scope != "run":
            step["scope"] = scope
    elif kind == "json":
        step["name"] = _var_name(raw, "name", "var")
        step["op"] = _enum(raw.get("op"), JSON_OPS, "get")
        step["input"] = _text(raw.get("input") if raw.get("input") is not None else raw.get("source"), MAX_TEXT)
        step["path"] = _text(raw.get("path"), 300)
        step["value"] = _text(raw.get("value"), MAX_TEXT)
        scope = _scope(raw)
        if scope != "run":
            step["scope"] = scope
    elif kind == "list":
        step["name"] = _var_name(raw, "name", "var", "list")
        step["op"] = _enum(raw.get("op"), LIST_OPS, "push")
        step["value"] = _text(raw.get("value"), MAX_TEXT)
        step["index"] = _int(raw.get("index"), 0, -MAX_LIST_ITEMS, MAX_LIST_ITEMS)
        step["count"] = _int(raw.get("count"), 0, 0, MAX_LIST_ITEMS)
        into = _var_name(raw, "into", required=False)
        if into:
            step["into"] = into
        scope = _scope(raw)
        if scope != "run":
            step["scope"] = scope
    elif kind == "file_create":
        step.update(path=_text(raw.get("path"), 500), content=_text(raw.get("content"), MAX_FILE_CHARS),
                    overwrite=_bool(raw.get("overwrite"), False),
                    require_confirm=_bool(raw.get("require_confirm"), False))
        _need(step, "path", "Укажите путь к файлу", "Enter the file path")
    elif kind == "file_edit":
        mode = str(raw.get("mode") or "replace_first").lower()
        if mode not in ("replace_first", "replace_all", "append", "prepend", "regex", "overwrite"):
            raise _err(f"Неизвестный режим правки «{mode[:20]}»", f"Unknown edit mode “{mode[:20]}”")
        step.update(path=_text(raw.get("path"), 500), mode=mode,
                    find=_text(raw.get("find"), MAX_TEXT), replace=_text(raw.get("replace"), MAX_TEXT),
                    append=_text(raw.get("append") or raw.get("content"), MAX_TEXT),
                    require_confirm=_bool(raw.get("require_confirm"), False))
        _need(step, "path", "Укажите путь к файлу", "Enter the file path")
        if mode in ("replace_first", "replace_all", "regex") and not step["find"]:
            raise _err("Укажите текст для замены", "Enter the text to replace")
        if mode == "regex":
            try:
                re.compile(step["find"])
            except re.error:
                raise _err("Регулярное выражение с ошибкой", "The regular expression is invalid") from None
        if _bool(raw.get("create")):
            step["create"] = True
    elif kind == "file_read":
        step["path"] = _text(raw.get("path"), 500)
        _need(step, "path", "Укажите путь к файлу", "Enter the file path")
        step["name"] = _var_name(raw, "name", "var", default="content")
        step["max_chars"] = _int(raw.get("max_chars"), 100_000, 100, MAX_FILE_CHARS)
        step["missing_ok"] = _bool(raw.get("missing_ok"), False)
        step["as"] = _enum(raw.get("as"), ("text", "lines", "json"), "text")
    elif kind == "file_info":
        step["path"] = _text(raw.get("path"), 500)
        _need(step, "path", "Укажите путь к файлу", "Enter the file path")
        step["name"] = _var_name(raw, "name", "var", default="file")
    elif kind == "file_list":
        step["path"] = _text(raw.get("path") or ".", 500)
        step["pattern"] = _text(raw.get("pattern") or "*", 120)
        step["recursive"] = _bool(raw.get("recursive"), False)
        step["dirs"] = _bool(raw.get("dirs"), False)
        step["sort"] = _enum(raw.get("sort"), ("name", "modified", "size"), "name")
        step["limit"] = _int(raw.get("limit"), 100, 1, 500)
        step["name"] = _var_name(raw, "name", "var", default="files")
    elif kind in ("file_copy", "file_move"):
        step.update(path=_text(raw.get("path"), 500), to=_text(raw.get("to") or raw.get("target"), 500),
                    overwrite=_bool(raw.get("overwrite"), False))
        _need(step, "path", "Укажите исходный файл", "Enter the source file")
        _need(step, "to", "Укажите, куда положить файл", "Enter the destination")
    elif kind == "file_delete":
        step.update(path=_text(raw.get("path"), 500), missing_ok=_bool(raw.get("missing_ok"), True))
        _need(step, "path", "Укажите файл", "Enter the file")
    elif kind == "folder_create":
        step["path"] = _text(raw.get("path"), 500)
        _need(step, "path", "Укажите папку", "Enter the folder")
    elif kind == "csv_append":
        step.update(path=_text(raw.get("path"), 500), values=_text(raw.get("values") or raw.get("row"), MAX_TEXT),
                    header=_text(raw.get("header"), 2000),
                    delimiter=_enum(raw.get("delimiter"), (";", ",", "tab"), ";"))
        _need(step, "path", "Укажите CSV-файл", "Enter the CSV file")
        _need(step, "values", "Перечислите значения строки через «|»", "List the row values separated by “|”")
    elif kind == "zip":
        step.update(path=_text(raw.get("path") or ".", 500), to=_text(raw.get("to"), 500),
                    overwrite=_bool(raw.get("overwrite"), True))
        _need(step, "to", "Укажите имя архива, например backups/notes-${today}.zip",
              "Enter the archive name, e.g. backups/notes-${today}.zip")
    elif kind == "if":
        step["cond"] = _normalize_cond(raw.get("cond") or raw.get("if") or {})
        extra = raw.get("conds") if isinstance(raw.get("conds"), list) else []
        if extra:
            step["conds"] = [_normalize_cond(c) for c in extra[:8]]
            step["match"] = "any" if str(raw.get("match") or "all") == "any" else "all"
        step["then"] = sub("then") if raw.get("then") is not None else sub("yes")
        step["else"] = sub("else") if raw.get("else") is not None else sub("no")
    elif kind == "repeat":
        cond = raw.get("while") or raw.get("cond")
        step["times"] = _int(raw.get("times"), 0, 0, MAX_REPEAT)
        if cond is not None:
            step["while"] = _normalize_cond(cond)
        if not step["times"] and "while" not in step:
            raise _err("Укажите число повторов или условие", "Set the repeat count or a condition")
        step["steps"] = sub("steps") if raw.get("steps") is not None else sub("do")
    elif kind == "foreach":
        step["items"] = _text(raw.get("items") or raw.get("list"), MAX_TEXT)
        _need(step, "items", "Укажите, что перебирать: ${list} или строки текста",
              "Say what to iterate over: ${list} or lines of text")
        step["as"] = _var_name(raw, "as", default="item")
        step["limit"] = _int(raw.get("limit"), 50, 1, MAX_FOREACH)
        step["steps"] = sub("steps") if raw.get("steps") is not None else sub("body")
    elif kind == "try":
        step["steps"] = sub("steps")
        step["catch"] = sub("catch")
        if not step["steps"]:
            raise _err("В блоке «Попробовать» нет шагов", "The “Try” block has no steps")
    elif kind == "wait":
        step["seconds"] = _int(raw.get("seconds"), 5, 1, MAX_WAIT)
    elif kind == "stop":
        step["status"] = "fail" if str(raw.get("status") or "ok") in ("fail", "error") else "ok"
        step["text"] = _text(raw.get("text") or raw.get("message"), 2000)
    elif kind == "log":
        step["text"] = _text(raw.get("text") or raw.get("message"), 2000)
        _need(step, "text", "Введите текст записи журнала", "Enter the log text")
    elif kind == "macro":
        if _text(raw.get("name"), 60):
            step["name"] = _text(raw.get("name"), 60)
        step["steps"] = sub("steps")
        if not step["steps"]:
            raise _err("Макрос без шагов", "A macro without steps")
    elif kind == "macro_call":
        step["macro_id"] = _text(raw.get("macro_id") or raw.get("macro") or raw.get("id"), 60)
        _need(step, "macro_id", "Выберите макрос из библиотеки", "Choose a macro from the library")
    elif kind == "run":
        argv = raw.get("argv") or raw.get("command") or []
        if isinstance(argv, str):
            argv = [argv]
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
            raise _err("Команда — список слов без shell", "The command is a list of words, no shell")
        step.update(argv=[_text(a, 500) for a in argv[:32]],
                    timeout=_int(raw.get("timeout"), RUN_TIMEOUT_DEFAULT, 1, RUN_TIMEOUT_MAX),
                    require_confirm=_bool(raw.get("require_confirm"), True))
        name = _var_name(raw, "name", "into", required=False)
        if name:
            step["name"] = name
        if _text(raw.get("cwd"), 300).strip():
            step["cwd"] = _text(raw.get("cwd"), 300).strip()
    elif kind == "system":
        step["name"] = _var_name(raw, "name", "var", default="value")
        step["metric"] = _enum(raw.get("metric"), SYSTEM_METRICS, "cpu_pct")
        step["arg"] = _text(raw.get("arg") or raw.get("process"), 200)
        if step["metric"] == "process" and not step["arg"].strip():
            raise _err("Укажите имя программы, например chrome.exe", "Enter a program name, e.g. chrome.exe")
    elif kind == "open_url":
        step["url"] = _text(raw.get("url"), 2000)
        _need(step, "url", "Укажите адрес https://…", "Enter an https://… address")
    elif kind == "integration":
        from .schedule_integrations import normalize as _norm
        try:
            step.update(_norm(raw.get("tool"), raw.get("args", {})))
        except Exception as exc:  # noqa: BLE001 - текст ошибки уже пользовательский
            raise _err(str(exc), str(exc)) from None
        step["type"] = "integration"
        step["require_confirm"] = _bool(raw.get("require_confirm"), False)
        name = _var_name(raw, "name", "into", required=False)
        if name:
            step["name"] = name
    elif kind == "http":
        step["method"] = _enum(str(raw.get("method") or "GET").upper(), HTTP_METHODS, "GET")
        step["url"] = _text(raw.get("url"), 2000)
        _need(step, "url", "Укажите адрес https://…", "Enter an https://… address")
        headers = raw.get("headers")
        if isinstance(headers, str):
            try:
                headers = json.loads(headers) if headers.strip() else {}
            except ValueError:
                raise _err("Заголовки HTTP — некорректный JSON", "The HTTP headers are invalid JSON") from None
        step["headers"] = {str(k)[:80]: _text(v, 2000) for k, v in (headers or {}).items()} \
            if isinstance(headers, dict) else {}
        step["body"] = _text(raw.get("body"), MAX_TEXT)
        step["parse"] = _enum(raw.get("parse"), ("auto", "text", "json"), "auto")
        step["timeout"] = _int(raw.get("timeout"), 20, 1, 60)
        step["name"] = _var_name(raw, "name", "into", default="response")
    elif kind == "telegram":
        step["text"] = _text(raw.get("text") or raw.get("message"), 4000)
        _need(step, "text", "Введите текст сообщения", "Enter the message text")
        step["silent"] = _bool(raw.get("silent"), False)
    elif kind == "notify":
        step["text"] = _text(raw.get("text") or raw.get("message"), MAX_TEXT)
        if not step["text"].strip():
            raise _err("Введите текст уведомления", "Enter the notification text")
    elif kind == "alert":
        step["title"] = _text(raw.get("title"), 120)
        step["text"] = _text(raw.get("text") or raw.get("message"), 2000)
        _need(step, "text", "Введите текст уведомления", "Enter the notification text")
        step["windows"] = _bool(raw.get("windows"), True)
        step["inbox"] = _bool(raw.get("inbox"), True)
    elif kind == "ai":
        step["prompt"] = _text(raw.get("prompt"), MAX_TEXT)
        _need(step, "prompt", "Напишите запрос к модели", "Write the prompt for the model")
        step["system"] = _text(raw.get("system"), 4000)
        step["model"] = _text(raw.get("model"), 300)
        step["json"] = _bool(raw.get("json"), False)
        step["name"] = _var_name(raw, "name", "into", default="answer")
        scope = _scope(raw)
        if scope != "run":
            step["scope"] = scope
    elif kind == "web_search":
        step["query"] = _text(raw.get("query"), 500)
        _need(step, "query", "Введите поисковый запрос", "Enter the search query")
        step["limit"] = _int(raw.get("limit"), 5, 1, 10)
        step["name"] = _var_name(raw, "name", "into", default="results")
    elif kind == "memubase":
        step["op"] = _enum(raw.get("op"), ("insert", "query", "count"), "insert")
        step["table"] = _text(raw.get("table"), 60)
        _need(step, "table", "Укажите таблицу MemuBase", "Enter the MemuBase table")
        step["row"] = _text(raw.get("row") or raw.get("rows"), MAX_TEXT)
        step["unique"] = _text(raw.get("unique"), 60)
        step["search"] = _text(raw.get("search"), 200)
        step["limit"] = _int(raw.get("limit"), 50, 1, 500)
        step["name"] = _var_name(raw, "name", "into", default="rows")
        if step["op"] == "insert" and not step["row"].strip():
            raise _err("Опишите строку: JSON или «поле=значение» по строкам",
                       "Describe the row: JSON or “field=value” lines")
    elif kind == "memory":
        step["text"] = _text(raw.get("text"), 2000)
        _need(step, "text", "Введите факт для памяти", "Enter the fact to remember")
        step["category"] = _text(raw.get("category"), 40)
    elif kind == "task":
        step["op"] = _enum(raw.get("op"), ("run", "enable", "disable", "toggle"), "run")
        step["task_id"] = _text(raw.get("task_id") or raw.get("task"), 120)
        _need(step, "task_id", "Выберите задачу", "Choose a task")
    elif kind == "module":
        step["module"] = _text(raw.get("module") or raw.get("module_id"), 60)
        step["action"] = _text(raw.get("action") or raw.get("action_id"), 60)
        _need(step, "module", "Выберите модуль", "Choose a module")
        _need(step, "action", "Выберите действие модуля", "Choose a module action")
    elif kind == "command":
        step["command"] = _text(raw.get("command") or raw.get("name"), 200)
        _need(step, "command", "Выберите команду быстрого доступа", "Choose a Quick Access command")
    elif kind == "assistant":
        step["action"] = _text(raw.get("action"), 120)
        _need(step, "action", "Укажите действие ассистента", "Enter the assistant action")
        params = raw.get("params")
        if isinstance(params, str):
            try:
                params = json.loads(params) if params.strip() else {}
            except ValueError:
                raise _err("Параметры действия — некорректный JSON", "The action parameters are invalid JSON") from None
        step["params"] = params if isinstance(params, dict) else {}
    return step


def normalize_scenario(raw: Any) -> Dict[str, Any]:
    """Проверка и канонизация сценария; ``ScenarioError`` — для пользователя."""
    if isinstance(raw, dict) and isinstance(raw.get("steps"), list):
        steps_raw, vars_raw = raw["steps"], raw.get("variables")
    elif isinstance(raw, list):
        steps_raw, vars_raw = raw, None
    else:
        raise _err("Сценарий — список шагов", "A scenario is a list of steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise _err("Добавьте хотя бы один шаг", "Add at least one step")
    counter = [0]
    steps = [_normalize_step(s, 0, counter) for s in steps_raw[:MAX_STEPS]]
    variables: Dict[str, Any] = {}
    if isinstance(vars_raw, dict):
        for key, value in list(vars_raw.items())[:MAX_VARIABLES]:
            if _NAME_RE.match(str(key)) and isinstance(value, (str, int, float, bool, list, dict)):
                try:
                    variables[str(key)] = _check_value(value)
                except ScenarioError:
                    continue
    return {"steps": steps, "variables": variables}


def normalize_steps(raw: Any) -> List[Dict[str, Any]]:
    """Шаги макроса библиотеки (без начальных переменных)."""
    return normalize_scenario(raw)["steps"]


def walk_steps(steps: Iterable[Dict[str, Any]]) -> Iterable[Dict[str, Any]]:
    """Все шаги дерева, включая вложенные в условия, циклы и группы."""
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        yield step
        for key in CONTAINERS.get(step.get("type"), ()):
            yield from walk_steps(step.get(key) or [])


def is_destructive(step: Dict[str, Any]) -> bool:
    kind = step.get("type")
    if kind in DESTRUCTIVE:
        return True
    if kind == "http" and str(step.get("method") or "GET").upper() not in READONLY_HTTP:
        return True
    return any(is_destructive(s) for key in CONTAINERS.get(kind, ()) for s in (step.get(key) or []))


def needs_confirmation(steps: Iterable[Dict[str, Any]]) -> bool:
    return any(is_destructive(s) for s in steps or [])


def disallowed_programs(steps: Iterable[Dict[str, Any]], allowlist: Any) -> List[str]:
    """Программы шагов «Запустить» (на любой глубине), которых нет в белом списке."""
    out: List[str] = []
    for step in walk_steps(steps):
        if step.get("type") == "run" and step.get("argv") and not _VAR_RE.search(step["argv"][0]) \
                and not check_run_allowed(step["argv"], allowlist):
            out.append(step["argv"][0])
    return out


def macro_ids(steps: Iterable[Dict[str, Any]]) -> List[str]:
    return [str(s.get("macro_id")) for s in walk_steps(steps) if s.get("type") == "macro_call"]


_TITLES = {
    "set": ("Переменная", "Variable"), "calc": ("Вычислить", "Calculate"), "text": ("Текст", "Text"),
    "random": ("Случайное значение", "Random value"), "date": ("Дата и время", "Date and time"),
    "json": ("JSON", "JSON"), "list": ("Список", "List"),
    "file_create": ("Создать файл", "Create a file"), "file_edit": ("Изменить файл", "Edit a file"),
    "file_read": ("Прочитать файл", "Read a file"), "file_info": ("Проверить файл", "Check a file"),
    "file_list": ("Список файлов", "List files"), "file_copy": ("Копировать файл", "Copy a file"),
    "file_move": ("Переместить файл", "Move a file"), "file_delete": ("Удалить файл", "Delete a file"),
    "folder_create": ("Создать папку", "Create a folder"), "csv_append": ("Строка в CSV", "Row to CSV"),
    "zip": ("Архив ZIP", "ZIP archive"),
    "if": ("Если", "If"), "repeat": ("Повторить", "Repeat"), "foreach": ("Для каждого", "For each"),
    "try": ("Попробовать", "Try"), "wait": ("Подождать", "Wait"), "stop": ("Завершить", "Stop"),
    "log": ("Запись в журнал", "Log entry"), "macro": ("Группа шагов", "Step group"),
    "macro_call": ("Макрос", "Macro"),
    "run": ("Запустить программу", "Run a program"), "system": ("Состояние системы", "System state"),
    "open_url": ("Открыть ссылку", "Open a link"),
    "integration": ("Интеграция", "Integration"), "http": ("HTTP-запрос", "HTTP request"),
    "telegram": ("Telegram", "Telegram"), "notify": ("Сообщение", "Message"),
    "alert": ("Уведомление", "Notification"), "ai": ("Спросить модель", "Ask a model"),
    "web_search": ("Веб-поиск", "Web search"), "memubase": ("MemuBase", "MemuBase"),
    "memory": ("Запомнить", "Remember"), "task": ("Другая задача", "Another task"),
    "module": ("Действие модуля", "Module action"), "command": ("Команда быстрого доступа", "Quick Access command"),
    "assistant": ("Действие ассистента", "Assistant action"),
}


def step_title(kind: str, lang: str = "ru") -> str:
    pair = _TITLES.get(kind, (kind, kind))
    return pair[1] if lang == "en" else pair[0]


def _short(text: Any, limit: int = 60) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[:limit - 1] + "…"


def describe_step(step: Dict[str, Any], lang: str = "ru") -> str:
    en = lang == "en"
    kind = str(step.get("type") or "")
    label = step.get("label") or ""
    prefix = f"{label}: " if label else ""
    t = (lambda ru, eng: eng if en else ru)  # noqa: E731
    scope = {"task": t(" (сохраняется)", " (saved)"), "global": t(" (общая)", " (shared)")}.get(step.get("scope"), "")
    if kind == "set":
        op = step.get("op")
        if op == "set":
            return prefix + t(f"Переменная {step['name']} = {_short(step.get('value'))}",
                              f"Set {step['name']} = {_short(step.get('value'))}") + scope
        if op in ("inc", "dec", "add", "mul"):
            sign = {"inc": "+", "add": "+", "dec": "−", "mul": "×"}[op]
            return prefix + f"{step['name']} {sign}= {step.get('by')}" + scope
        if op == "append":
            return prefix + t(f"Дописать к {step['name']}: {_short(step.get('value'), 40)}",
                              f"Append to {step['name']}: {_short(step.get('value'), 40)}") + scope
        if op == "toggle":
            return prefix + t(f"Переключить {step['name']}", f"Toggle {step['name']}") + scope
        return prefix + t(f"Удалить переменную {step['name']}", f"Delete variable {step['name']}") + scope
    if kind == "calc":
        return prefix + f"{step['name']} = {_short(step.get('expr'), 70)}" + scope
    if kind == "text":
        return prefix + t(f"Текст ({step.get('op')}) → {step['name']}", f"Text ({step.get('op')}) → {step['name']}") + scope
    if kind == "random":
        return prefix + t(f"Случайное ({step.get('mode')}) → {step['name']}", f"Random ({step.get('mode')}) → {step['name']}") + scope
    if kind == "date":
        return prefix + t(f"Дата ({step.get('mode')}) → {step['name']}", f"Date ({step.get('mode')}) → {step['name']}") + scope
    if kind == "json":
        return prefix + f"JSON {step.get('op')} {step.get('path') or ''} → {step['name']}".replace("  ", " ") + scope
    if kind == "list":
        return prefix + t(f"Список {step['name']}: {step.get('op')}", f"List {step['name']}: {step.get('op')}") + scope
    if kind == "file_create":
        return prefix + t(f"Создать файл {step['path']}", f"Create file {step['path']}")
    if kind == "file_edit":
        return prefix + t(f"Изменить файл {step['path']} ({step['mode']})", f"Edit file {step['path']} ({step['mode']})")
    if kind == "file_read":
        return prefix + t(f"Прочитать {step['path']} → {step['name']}", f"Read {step['path']} → {step['name']}")
    if kind == "file_info":
        return prefix + t(f"Проверить файл {step['path']}", f"Check file {step['path']}")
    if kind == "file_list":
        return prefix + t(f"Файлы {step['path']}/{step['pattern']} → {step['name']}",
                          f"Files {step['path']}/{step['pattern']} → {step['name']}")
    if kind in ("file_copy", "file_move"):
        verb = t("Копировать", "Copy") if kind == "file_copy" else t("Переместить", "Move")
        return prefix + f"{verb} {step['path']} → {step['to']}"
    if kind == "file_delete":
        return prefix + t(f"Удалить файл {step['path']}", f"Delete file {step['path']}")
    if kind == "folder_create":
        return prefix + t(f"Создать папку {step['path']}", f"Create folder {step['path']}")
    if kind == "csv_append":
        return prefix + t(f"Строка в {step['path']}", f"Row to {step['path']}")
    if kind == "zip":
        return prefix + t(f"Архив {step['path']} → {step['to']}", f"Archive {step['path']} → {step['to']}")
    if kind == "run":
        return prefix + t(f"Запустить: {' '.join(step['argv'][:2])}", f"Run: {' '.join(step['argv'][:2])}")
    if kind == "system":
        return prefix + t(f"Система: {step.get('metric')} → {step['name']}", f"System: {step.get('metric')} → {step['name']}")
    if kind == "open_url":
        return prefix + t(f"Открыть {_short(step.get('url'), 50)}", f"Open {_short(step.get('url'), 50)}")
    if kind == "integration":
        return prefix + t(f"Интеграция: {step.get('tool')}", f"Integration: {step.get('tool')}")
    if kind == "http":
        return prefix + f"HTTP {step.get('method')} {_short(step.get('url'), 50)}"
    if kind == "telegram":
        return prefix + f"Telegram: {_short(step.get('text'))}"
    if kind == "notify":
        return prefix + (step.get("text") or "")
    if kind == "alert":
        return prefix + t(f"Уведомление: {_short(step.get('text'))}", f"Notification: {_short(step.get('text'))}")
    if kind == "ai":
        return prefix + t(f"Модель: {_short(step.get('prompt'))} → {step['name']}", f"Model: {_short(step.get('prompt'))} → {step['name']}")
    if kind == "web_search":
        return prefix + t(f"Поиск: {_short(step.get('query'))}", f"Search: {_short(step.get('query'))}")
    if kind == "memubase":
        return prefix + f"MemuBase {step.get('op')} {step.get('table')}"
    if kind == "memory":
        return prefix + t(f"Запомнить: {_short(step.get('text'))}", f"Remember: {_short(step.get('text'))}")
    if kind == "task":
        ops = {"run": t("Запустить", "Run"), "enable": t("Включить", "Enable"), "disable": t("Выключить", "Disable"),
               "toggle": t("Переключить", "Toggle")}
        return prefix + t(f"{ops.get(step.get('op'), '')} задачу {step.get('task_id')}",
                          f"{ops.get(step.get('op'), '')} task {step.get('task_id')}")
    if kind == "module":
        return prefix + t(f"Модуль {step.get('module')}: {step.get('action')}", f"Module {step.get('module')}: {step.get('action')}")
    if kind == "command":
        return prefix + t(f"Команда: {step.get('command')}", f"Command: {step.get('command')}")
    if kind == "assistant":
        return prefix + t(f"Ассистент: {step.get('action')}", f"Assistant: {step.get('action')}")
    if kind == "if":
        return prefix + t("Если…", "If…")
    if kind == "repeat":
        return prefix + t(f"Повторить {step.get('times') or 'пока'}×", f"Repeat {step.get('times') or 'while'}×")
    if kind == "foreach":
        return prefix + t(f"Для каждого {step.get('as')} в {_short(step.get('items'), 40)}",
                          f"For each {step.get('as')} in {_short(step.get('items'), 40)}")
    if kind == "try":
        return prefix + t("Попробовать…", "Try…")
    if kind == "wait":
        return prefix + t(f"Подождать {step.get('seconds')} с", f"Wait {step.get('seconds')} s")
    if kind == "stop":
        return prefix + t("Завершить сценарий", "Stop the scenario")
    if kind == "log":
        return prefix + t(f"Журнал: {_short(step.get('text'))}", f"Log: {_short(step.get('text'))}")
    if kind == "macro_call":
        return prefix + t(f"Макрос {step.get('macro_id')}", f"Macro {step.get('macro_id')}")
    return prefix + (step.get("name") or ("Macro" if en else "Макрос"))


def describe_scenario(scenario: Dict[str, Any], lang: str = "ru") -> List[str]:
    return [describe_step(s, lang) for s in (scenario or {}).get("steps", [])]


def check_run_allowed(argv: List[str], allowlist: Any) -> bool:
    """Имя программы (или полный путь) должно быть в белом списке; пустой список ничего не разрешает."""
    if not argv:
        return False
    allowed = [str(a).strip() for a in (allowlist or []) if str(a or "").strip()]
    if not allowed:
        return False
    prog = argv[0].replace("\\", "/")
    base = prog.rsplit("/", 1)[-1].lower()
    for entry in allowed:
        clean = entry.replace("\\", "/").strip()
        if "/" in clean:
            if prog.lower() == clean.lower():
                return True
        elif base == clean.lower():
            return True
    return False


def _cond_holds(cond: Dict[str, Any], variables: Dict[str, Any]) -> bool:
    left_raw = substitute(cond["left"], variables)
    right_raw = substitute(cond["right"], variables)
    op = cond["op"]
    if op == "empty":
        return not left_raw.strip()
    if op == "not_empty":
        return bool(left_raw.strip())
    if op == "exists":
        name = cond["left"].strip()
        match = _VAR_RE.fullmatch(name)
        return resolve_var(match.group(1) if match else name, variables) is not None
    if op in ("contains", "not_contains"):
        found = right_raw in left_raw
        return found if op == "contains" else not found
    if op == "starts_with":
        return left_raw.startswith(right_raw)
    if op == "ends_with":
        return left_raw.endswith(right_raw)
    if op == "regex":
        try:
            return re.search(right_raw[:300], left_raw[:100_000]) is not None
        except re.error:
            return False
    if op == "is_number":
        return _num(left_raw) is not None and left_raw.strip() != ""
    if op == "is_true":
        return left_raw.strip().lower() in ("1", "true", "yes", "да", "on")
    if op == "is_false":
        return left_raw.strip().lower() in ("", "0", "false", "no", "нет", "off")
    if op == "in_list":
        value = raw_value(cond["right"], variables)
        if isinstance(value, list):
            return left_raw in [_plain(v) for v in value]
        return left_raw in [part.strip() for part in re.split(r"[,\n]", right_raw)]
    ln, rn = _num(left_raw), _num(right_raw)
    if ln is not None and rn is not None and left_raw.strip() and right_raw.strip():
        left: Any = ln
        right: Any = rn
    else:
        left, right = left_raw, right_raw
    if op == "eq":
        return left == right
    if op == "ne":
        return left != right
    if op == "gt":
        return left > right
    if op == "ge":
        return left >= right
    if op == "lt":
        return left < right
    return left <= right


def _step_cond(step: Dict[str, Any], variables: Dict[str, Any]) -> bool:
    first = _cond_holds(step["cond"], variables)
    extra = step.get("conds") or []
    if not extra:
        return first
    results = [first] + [_cond_holds(c, variables) for c in extra]
    return any(results) if step.get("match") == "any" else all(results)


# --------------------------------------------------------------- выполнение
MAX_RUNTIME = 15 * 60
_WEEKDAYS_RU = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
_WEEKDAYS_EN = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_FORMAT_TOKENS = (("YYYY", "%Y"), ("YY", "%y"), ("MM", "%m"), ("DD", "%d"), ("HH", "%H"), ("mm", "%M"), ("ss", "%S"))


def _fmt_date(moment: datetime, fmt: str, lang: str) -> str:
    fmt = (fmt or "").strip()
    if not fmt:
        return moment.strftime("%Y-%m-%d %H:%M")
    if "%" not in fmt:
        for token, code in _FORMAT_TOKENS:
            fmt = fmt.replace(token, code)
    fmt = fmt.replace("%A", (_WEEKDAYS_EN if lang == "en" else _WEEKDAYS_RU)[moment.weekday()])
    try:
        return moment.strftime(fmt)[:200]
    except ValueError:
        raise _err("Неверный формат даты", "Invalid date format") from None


def _parse_date(text: str) -> datetime:
    raw = str(text or "").strip()
    if not raw:
        return datetime.now()
    if re.fullmatch(r"\d{9,11}(\.\d+)?", raw):
        return datetime.fromtimestamp(float(raw))
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%d.%m.%Y %H:%M", "%d.%m.%Y", "%H:%M"):
        try:
            parsed = datetime.strptime(raw[:19], fmt)
            if fmt == "%H:%M":
                now = datetime.now()
                parsed = now.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
            return parsed
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        raise _err(f"Не удалось понять дату «{raw[:40]}»", f"Could not read the date “{raw[:40]}”") from None


def _items_of(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [{"key": k, "value": v} for k, v in value.items()]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return list(range(max(0, min(MAX_FOREACH, int(value)))))
    text = str(value or "").strip()
    if not text:
        return []
    if text[:1] == "[":
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed
        except ValueError:
            pass
    return [line.strip() for line in text.splitlines() if line.strip()]


def _split_items(text: str) -> List[str]:
    text = str(text or "")
    parts = text.splitlines() if "\n" in text else text.split(",")
    return [p.strip() for p in parts if p.strip()]


def _json_path_get(value: Any, path: str) -> Any:
    if not path.strip():
        return value
    current = value
    for part in re.split(r"\.|\[(\d+)\]", path.strip()):
        if part in (None, ""):
            continue
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and re.fullmatch(r"-?\d+", part):
            index = int(part)
            current = current[index] if -len(current) <= index < len(current) else None
        else:
            return None
    return current


def _json_path_set(value: Any, path: str, new: Any, *, delete: bool = False) -> Any:
    parts = [p for p in re.split(r"\.|\[(\d+)\]", path.strip()) if p not in (None, "")]
    if not parts:
        return new
    root = value if isinstance(value, (dict, list)) else {}
    current = root
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if isinstance(current, list) and re.fullmatch(r"-?\d+", part):
            pos = int(part)
            if not -len(current) <= pos < len(current):
                if last and not delete and pos == len(current):
                    current.append(new)
                    return root
                raise _err("Индекс вне списка", "The index is outside the list")
            if last:
                if delete:
                    current.pop(pos)
                else:
                    current[pos] = new
                return root
            current = current[pos]
        elif isinstance(current, dict):
            if last:
                if delete:
                    current.pop(part, None)
                else:
                    current[part] = new
                return root
            if not isinstance(current.get(part), (dict, list)):
                current[part] = {}
            current = current[part]
        else:
            raise _err("Путь JSON не подходит к данным", "The JSON path does not fit the data")
    return root


def _default_services(lang: str) -> Any:
    from .flow_engine import Services
    return Services(lang=lang)


class _Runner:
    def __init__(self, *, base_dir: Path, allowlist: Any, confirmed: bool,
                 variables: Dict[str, Any], integration_runner: Any, lang: str = "ru",
                 services: Any = None, task: Optional[Dict[str, Any]] = None, dry: bool = False,
                 global_vars: Optional[Dict[str, Any]] = None, macros: Optional[Callable[[str], Any]] = None) -> None:
        self.base = base_dir
        self.allowlist = allowlist
        self.confirmed = confirmed
        self.variables = dict(variables)
        self.integration_runner = integration_runner
        self.lang = lang
        self._services = services
        self.task = task or {}
        self.dry = dry
        self.global_vars = dict(global_vars or {})
        self.macro_loader = macros
        self.macro_stack: List[str] = []
        self.log: List[Dict[str, Any]] = []
        self.lines: List[str] = []
        self.executed = 0
        self.started = time.monotonic()
        self.last: Dict[str, Any] = {"ok": True, "text": "", "output": ""}
        self.error = ""
        self.dirty: Dict[str, set] = {"task": set(), "global": set()}
        self.deleted: Dict[str, set] = {"task": set(), "global": set()}
        self.stopped: Optional[_Stop] = None

    # ------------------------------------------------------------- помощники
    def tr(self, ru: str, en: str) -> str:
        return en if self.lang == "en" else ru

    @property
    def services(self) -> Any:
        if self._services is None:
            self._services = _default_services(self.lang)
        return self._services

    def _builtins(self) -> Dict[str, Any]:
        now = datetime.now()
        return {"now": now.strftime("%Y-%m-%d %H:%M"), "today": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H:%M"), "timestamp": int(time.time()),
                "weekday": (_WEEKDAYS_EN if self.lang == "en" else _WEEKDAYS_RU)[now.weekday()],
                "task": {"id": str(self.task.get("id") or ""), "name": str(self.task.get("name") or "")}}

    def _vars(self) -> Dict[str, Any]:
        return {**self._builtins(), **self.global_vars, **self.variables, "global": dict(self.global_vars),
                "last": self.last, "error": self.error}

    def sub(self, value: Any) -> str:
        return substitute(value, self._vars())

    def raw(self, value: Any) -> Any:
        return raw_value(value, self._vars())

    def _note(self, step: Dict[str, Any], ok: bool, ms: float, detail: str = "", error: str = "",
              **extra: Any) -> None:
        entry: Dict[str, Any] = {"type": step.get("type"), "ok": ok, "ms": round(ms, 1), "detail": str(detail)[:300]}
        if step.get("label"):
            entry["label"] = step["label"]
        if error:
            entry["error"] = str(error)[:300]
        entry.update({k: v for k, v in extra.items() if v not in (None, "", False)})
        self.log.append(entry)

    def _ms(self, started: float) -> float:
        return (time.monotonic() - started) * 1000

    def _assign(self, name: str, value: Any, scope: str = "run") -> None:
        value = _check_value(value)
        if name not in self.variables and len(self.variables) >= MAX_VARIABLES * 2:
            raise _err("Слишком много переменных", "Too many variables")
        self.variables[name] = value
        scope = scope or "run"
        if scope == "global":
            self.global_vars[name] = value
        if scope in ("task", "global"):
            self.dirty[scope].add(name)
            self.deleted[scope].discard(name)

    def _ok(self, step: Dict[str, Any], started: float, detail: str = "", *, text: str = "",
            output: Any = "", line: str = "", **extra: Any) -> bool:
        self.last = {"ok": True, "text": str(text)[:MAX_OUTPUT_CHARS],
                     "output": output if isinstance(output, (list, dict)) else str(output)[:MAX_OUTPUT_CHARS]}
        if line:
            self.lines.append(line[:4000])
        self._note(step, True, self._ms(started), detail, **extra)
        return True

    def _fail(self, step: Dict[str, Any], started: float, detail: str, error: str) -> bool:
        self.last = {"ok": False, "text": "", "output": ""}
        self.error = str(error)[:500]
        self._note(step, False, self._ms(started), detail, error)
        return False

    def path(self, rel: Any, *, allow_root: bool = False) -> Path:
        return _resolve_path(self.base, self.sub(rel), allow_root=allow_root)

    def rel(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.base.resolve()).as_posix() or "."
        except ValueError:
            return path.name

    # ------------------------------------------------------------- ход сценария
    async def run_steps(self, steps: List[Dict[str, Any]], depth: int) -> bool:
        ok = True
        for step in steps:
            if self.executed >= MAX_EXECUTED:
                self._note(step, False, 0, "limit", "Превышен лимит шагов / Step limit exceeded")
                return False
            if time.monotonic() - self.started > MAX_RUNTIME:
                self._note(step, False, 0, "limit", "Превышено время сценария (15 мин) / Scenario time limit (15 min)")
                return False
            self.executed += 1
            if step.get("disabled"):
                self._note(step, True, 0, self.tr("шаг выключен", "step disabled"), skipped=True)
                continue
            if not await self.run_step(step, depth):
                if step.get("continue_on_error"):
                    continue
                ok = False
                break
        return ok

    async def run_step(self, step: Dict[str, Any], depth: int) -> bool:
        started = time.monotonic()
        kind = step["type"]
        try:
            if kind == "if":
                branch = step["then"] if _step_cond(step, self._vars()) else step["else"]
                result = await self.run_steps(branch, depth + 1)
                self.last = {"ok": result, "text": "", "output": ""}
                self._note(step, result, self._ms(started), "if")
                return result
            if kind == "repeat":
                return await self._repeat(step, depth, started)
            if kind == "foreach":
                return await self._foreach(step, depth, started)
            if kind == "try":
                return await self._try(step, depth, started)
            if kind == "macro":
                result = await self.run_steps(step["steps"], depth + 1)
                self._note(step, result, self._ms(started), step.get("label") or step.get("name") or "macro")
                return result
            if kind == "macro_call":
                return await self._macro_call(step, depth, started)
            if kind == "stop":
                text = self.sub(step.get("text"))
                ok = step.get("status") != "fail"
                self._note(step, ok, self._ms(started), text[:120] or ("stop" if ok else "fail"))
                raise _Stop(ok, text)
            destructive = is_destructive(step)
            if destructive and not self.confirmed and not self.dry:
                return self._fail(step, started, step.get("path") or " ".join(step.get("argv", [])[:1]) or kind,
                                  "Нужно подтверждение при создании правила / Confirmation at rule creation is required")
            if self.dry and (kind in SIDE_EFFECT or destructive):
                return self._dry(step, started)
            handler = getattr(self, "_do_" + kind, None)
            if handler is None:
                return self._fail(step, started, "", "Unknown step")
            result = handler(step, started)
            if asyncio.iscoroutine(result):
                result = await result
            return bool(result)
        except _Stop:
            raise
        except ScenarioError as exc:
            return self._fail(step, started, "", exc.text(self.lang))
        except asyncio.CancelledError:
            raise
        except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile, UnicodeError) as exc:
            return self._fail(step, started, "", f"{type(exc).__name__}: {exc}"[:200])
        except Exception as exc:  # noqa: BLE001 - любой сбой шага записывается в журнал
            text = getattr(exc, "args", [""])[0] if getattr(exc, "args", None) else ""
            return self._fail(step, started, "", (str(text) or type(exc).__name__)[:300])

    def _dry(self, step: Dict[str, Any], started: float) -> bool:
        detail = describe_step(step, self.lang)
        for key in ("name", "into"):
            if step.get(key) and step["type"] in ("ai", "web_search", "http", "run", "integration", "memubase"):
                self.variables.setdefault(step[key], "")
        if step["type"] == "run" and not check_run_allowed([self.sub(a) for a in step["argv"]], self.allowlist):
            return self._fail(step, started, detail, "Программа не в белом списке / The program is not in the allowlist")
        self.last = {"ok": True, "text": "", "output": ""}
        self._note(step, True, self._ms(started), detail, dry=True)
        return True

    # ------------------------------------------------------------- данные
    def _do_set(self, step: Dict[str, Any], started: float) -> bool:
        name, op, scope = step["name"], step["op"], step.get("scope", "run")
        current = self.variables.get(name, self.global_vars.get(name))
        if op == "set":
            value: Any = _coerce(self.sub(step.get("value", "")))
            if isinstance(value, str) and _VAR_RE.fullmatch(str(step.get("value", "")).strip()):
                raw = self.raw(step.get("value", ""))
                value = raw if isinstance(raw, (list, dict)) else value
        elif op == "append":
            addition = self.sub(step.get("value", ""))
            if isinstance(current, list):
                value = current + [_coerce(addition)]
            else:
                value = _plain(current) + addition
        elif op == "toggle":
            value = not _bool(current if not isinstance(current, str) else current.strip().lower() in ("1", "true", "yes", "да", "on"))
        elif op == "delete":
            self.variables.pop(name, None)
            if scope == "global":
                self.global_vars.pop(name, None)
            if scope in ("task", "global"):
                self.deleted[scope].add(name)
                self.dirty[scope].discard(name)
            return self._ok(step, started, name)
        else:
            base = _num(current) or 0.0
            by = float(step.get("by", 1))
            total = base * by if op == "mul" else base + by * (1 if op in ("inc", "add") else -1)
            value = _tidy_number(total)
        self._assign(name, value, scope)
        shown = _plain(value, 300)
        return self._ok(step, started, name, text=shown, output=shown)

    def _do_calc(self, step: Dict[str, Any], started: float) -> bool:
        expr = self.sub(step["expr"]) if "${" in step["expr"] else step["expr"]
        value = calc(expr, self._vars())
        if "decimals" in step and isinstance(value, (int, float)) and not isinstance(value, bool):
            value = _tidy_number(round(float(value), step["decimals"]))
        self._assign(step["name"], value, step.get("scope", "run"))
        return self._ok(step, started, f"{step['name']} = {_plain(value, 80)}", text=_plain(value), output=_plain(value))

    def _do_text(self, step: Dict[str, Any], started: float) -> bool:
        op = step["op"]
        source = self.raw(step.get("input", ""))
        text = source if isinstance(source, str) else _plain(source, MAX_VALUE_CHARS)
        a1, a2 = self.sub(step.get("arg1", "")), self.sub(step.get("arg2", ""))
        value: Any
        if op == "upper":
            value = text.upper()
        elif op == "lower":
            value = text.lower()
        elif op == "title":
            value = text.title()
        elif op == "trim":
            value = text.strip(a1) if a1 else text.strip()
        elif op == "replace":
            value = text.replace(a1, a2) if a1 else text
        elif op in ("regex_replace", "regex_find", "regex_all"):
            pattern = re.compile(a1[:300])
            chunk = text[:200_000]
            if op == "regex_replace":
                value = pattern.sub(a2, chunk)
            elif op == "regex_find":
                found = pattern.search(chunk)
                value = (found.group(1) if found and found.groups() else found.group(0)) if found else ""
            else:
                value = [m.group(1) if m.groups() else m.group(0) for m in pattern.finditer(chunk)][:MAX_LIST_ITEMS]
        elif op == "slice":
            start = _int(a1, 0, -MAX_VALUE_CHARS, MAX_VALUE_CHARS)
            end = _int(a2, len(text), -MAX_VALUE_CHARS, MAX_VALUE_CHARS) if a2.strip() else len(text)
            value = text[start:end]
        elif op == "length":
            value = len(source) if isinstance(source, (list, dict)) else len(text)
        elif op == "split":
            value = [p for p in text.split(a1 or ",")][:MAX_LIST_ITEMS]
            value = [p.strip() for p in value] if not a2 else value
        elif op == "lines":
            value = [line for line in text.splitlines() if line.strip()][:MAX_LIST_ITEMS]
        elif op == "words":
            value = len(text.split())
        elif op == "first_line":
            value = next((line for line in text.splitlines() if line.strip()), "")
        elif op == "last_line":
            value = next((line for line in reversed(text.splitlines()) if line.strip()), "")
        elif op == "contains":
            value = a1 in text
        elif op == "starts_with":
            value = text.startswith(a1)
        elif op == "ends_with":
            value = text.endswith(a1)
        elif op == "pad":
            width = _int(a1, 2, 1, 200)
            value = text.rjust(width, (a2 or "0")[:1])
        elif op == "url_encode":
            value = quote(text, safe="")
        elif op == "url_decode":
            value = unquote(text)
        elif op == "base64_encode":
            value = base64.b64encode(text.encode("utf-8")).decode("ascii")
        elif op == "base64_decode":
            value = base64.b64decode(text.encode("ascii"), validate=False).decode("utf-8", "replace")
        elif op == "sha256":
            value = hashlib.sha256(text.encode("utf-8")).hexdigest()
        elif op == "md5":
            value = hashlib.md5(text.encode("utf-8")).hexdigest()  # noqa: S324 - контрольная сумма, не защита
        elif op == "number":
            number = _num(text.replace(",", ".").replace(" ", "").replace("\u00a0", ""))
            if number is None:
                found = re.search(r"[-+]?\d+(?:[.,]\d+)?", text)
                number = _num(found.group(0).replace(",", ".")) if found else None
            value = _tidy_number(number) if number is not None else ""
        else:  # template
            value = text
        self._assign(step["name"], value, step.get("scope", "run"))
        shown = _plain(value, 200)
        return self._ok(step, started, f"{step['name']} ← {op}", text=shown, output=value)

    def _do_random(self, step: Dict[str, Any], started: float) -> bool:
        mode = step["mode"]
        rng = random.SystemRandom()
        lo, hi = float(step.get("min", 1)), float(step.get("max", 100))
        if lo > hi:
            lo, hi = hi, lo
        value: Any
        if mode == "int":
            value = rng.randint(int(math.ceil(lo)), int(math.floor(hi)))
        elif mode == "float":
            value = round(rng.uniform(lo, hi), 4)
        elif mode == "bool":
            value = rng.random() < 0.5
        elif mode == "uuid":
            value = uuid.uuid4().hex
        elif mode == "password":
            alphabet = string.ascii_letters + string.digits + "-_!@#%"
            value = "".join(secrets.choice(alphabet) for _ in range(step.get("length", 12)))
        else:
            source = self.raw(step.get("items", ""))
            items = source if isinstance(source, list) else _split_items(self.sub(step.get("items", "")))
            if not items:
                raise _err("Список вариантов пуст", "The list of options is empty")
            if mode == "pick":
                value = rng.choice(items)
            else:
                value = list(items)
                rng.shuffle(value)
        self._assign(step["name"], value, step.get("scope", "run"))
        detail = step["name"] if mode == "password" else f"{step['name']} = {_plain(value, 60)}"
        return self._ok(step, started, detail, text=_plain(value), output=_plain(value))

    def _do_date(self, step: Dict[str, Any], started: float) -> bool:
        mode = step["mode"]
        base = _parse_date(self.sub(step.get("value", "")))
        fmt = self.sub(step.get("format", ""))
        value: Any
        amount = float(step.get("amount") or 0)
        if mode == "now":
            value = _fmt_date(datetime.now(), fmt, self.lang)
        elif mode == "today":
            value = _fmt_date(datetime.now(), fmt or "%Y-%m-%d", self.lang)
        elif mode == "timestamp":
            value = int(base.timestamp())
        elif mode == "weekday":
            value = (_WEEKDAYS_EN if self.lang == "en" else _WEEKDAYS_RU)[base.weekday()]
        elif mode == "format":
            value = _fmt_date(base, fmt, self.lang)
        elif mode == "add":
            delta = {"minutes": timedelta(minutes=amount), "hours": timedelta(hours=amount),
                     "days": timedelta(days=amount), "weeks": timedelta(weeks=amount)}[step.get("unit", "days")]
            value = _fmt_date(base + delta, fmt or ("%Y-%m-%d" if step.get("unit") in ("days", "weeks") else ""), self.lang)
        elif mode in ("diff_days", "diff_minutes"):
            other = _parse_date(self.sub(step.get("value2", "")))
            seconds = (other - base).total_seconds()
            value = _tidy_number(round(seconds / (86400 if mode == "diff_days" else 60), 2))
        elif mode == "start_of_week":
            start = (base - timedelta(days=base.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
            value = _fmt_date(start, fmt or "%Y-%m-%d", self.lang)
        elif mode == "start_of_month":
            value = _fmt_date(base.replace(day=1, hour=0, minute=0, second=0, microsecond=0), fmt or "%Y-%m-%d", self.lang)
        else:  # parse
            value = _fmt_date(base, fmt or "%Y-%m-%d %H:%M", self.lang)
        self._assign(step["name"], value, step.get("scope", "run"))
        return self._ok(step, started, f"{step['name']} = {_plain(value, 60)}", text=_plain(value), output=_plain(value))

    def _json_source(self, raw_input: str) -> Any:
        value = self.raw(raw_input)
        if isinstance(value, (dict, list)):
            return value
        text = str(value or "").strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except ValueError:
            raise _err("Это не JSON — проверьте исходные данные", "This is not JSON — check the source data") from None

    def _do_json(self, step: Dict[str, Any], started: float) -> bool:
        op, path = step["op"], self.sub(step.get("path", ""))
        value: Any
        if op == "parse":
            value = self._json_source(step.get("input", ""))
        elif op == "stringify":
            source = self.raw(step.get("input", ""))
            value = json.dumps(source, ensure_ascii=False, indent=2) if isinstance(source, (dict, list)) else _plain(source)
        else:
            data = self._json_source(step.get("input", ""))
            if op == "get":
                value = _json_path_get(data, path)
            elif op == "keys":
                target = _json_path_get(data, path)
                value = list(target.keys())[:MAX_LIST_ITEMS] if isinstance(target, dict) else []
            elif op == "length":
                target = _json_path_get(data, path)
                value = len(target) if isinstance(target, (dict, list, str)) else 0
            elif op == "merge":
                extra = self._json_source(step.get("value", ""))
                value = {**data, **extra} if isinstance(data, dict) and isinstance(extra, dict) else data
            elif op == "delete":
                value = _json_path_set(json.loads(json.dumps(data)), path, None, delete=True)
            else:  # set
                new = _coerce(self.sub(step.get("value", "")))
                if isinstance(new, str) and _VAR_RE.fullmatch(str(step.get("value", "")).strip()):
                    raw = self.raw(step.get("value", ""))
                    new = raw if isinstance(raw, (list, dict)) else new
                value = _json_path_set(json.loads(json.dumps(data)), path, new)
        self._assign(step["name"], value, step.get("scope", "run"))
        return self._ok(step, started, f"{step['name']} ← json.{op}", text=_plain(value, 200), output=value)

    def _do_list(self, step: Dict[str, Any], started: float) -> bool:
        name, op = step["name"], step["op"]
        current = self.variables.get(name, self.global_vars.get(name))
        items = list(current) if isinstance(current, list) else _items_of(current) if current not in (None, "") else []
        value_raw = step.get("value", "")
        value: Any = self.raw(value_raw) if _VAR_RE.fullmatch(str(value_raw).strip()) else _coerce(self.sub(value_raw))
        result: Any = None
        index = int(step.get("index") or 0)
        if op == "push":
            items.append(value)
        elif op == "unshift":
            items.insert(0, value)
        elif op == "pop":
            result = items.pop() if items else ""
        elif op == "shift":
            result = items.pop(0) if items else ""
        elif op == "clear":
            items = []
        elif op == "length":
            result = len(items)
        elif op == "get":
            result = items[index] if -len(items) <= index < len(items) and items else ""
        elif op == "remove":
            items = [item for item in items if _plain(item) != _plain(value)]
        elif op == "contains":
            result = any(_plain(item) == _plain(value) for item in items)
        elif op == "unique":
            seen, out = set(), []
            for item in items:
                key = _plain(item)
                if key not in seen:
                    seen.add(key)
                    out.append(item)
            items = out
        elif op == "sort":
            items = sorted(items, key=lambda v: (0, _num(v)) if _num(v) is not None else (1, _plain(v).lower()))
        elif op == "reverse":
            items = list(reversed(items))
        elif op == "join":
            sep = self.sub(value_raw) if value_raw else ", "
            result = sep.join(_plain(item) for item in items)
        elif op == "slice":
            count = int(step.get("count") or 0) or len(items)
            result = items[index:index + count]
        elif op in ("sum", "min", "max"):
            numbers = [_num(item) for item in items if _num(item) is not None]
            result = _tidy_number({"sum": sum, "min": min, "max": max}[op](numbers)) if numbers else 0
        else:  # from_text
            source = self.raw(value_raw)
            items = source if isinstance(source, list) else _items_of(self.sub(value_raw))
        scope = step.get("scope", "run")
        if op in ("push", "unshift", "pop", "shift", "clear", "remove", "unique", "sort", "reverse", "from_text"):
            self._assign(name, items[-MAX_LIST_ITEMS:], scope)
        if result is not None:
            if step.get("into"):
                self._assign(step["into"], result)
            out_text = _plain(result, 300)
        else:
            out_text = str(len(items))
        return self._ok(step, started, f"{name}.{op}", text=out_text, output=result if result is not None else items)

    # ------------------------------------------------------------- файлы
    def _write_text(self, target: Path, text: str, *, bom: bool = False) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".memuai-scenario.tmp")
        tmp.write_text(text, encoding="utf-8-sig" if bom else "utf-8")
        os.replace(tmp, target)

    def _do_file_create(self, step: Dict[str, Any], started: float) -> bool:
        rel = self.sub(step["path"])
        target = _resolve_path(self.base, rel)
        content = self.sub(step["content"])
        if len(content) > MAX_FILE_CHARS:
            raise _err("Содержимое файла слишком большое", "The file content is too large")
        if target.exists() and not step["overwrite"]:
            return self._fail(step, started, str(target.name), "Файл уже существует / The file already exists")
        self._write_text(target, content)
        return self._ok(step, started, rel, text=rel,
                        line=self.tr(f"Файл создан: {rel}", f"File created: {rel}"))

    def _do_file_edit(self, step: Dict[str, Any], started: float) -> bool:
        rel = self.sub(step["path"])
        target = _resolve_path(self.base, rel)
        mode = step["mode"]
        if not target.is_file():
            if step.get("create") and mode in ("append", "prepend", "overwrite"):
                text = ""
            else:
                raise _err("Файл не найден", "The file was not found")
        else:
            if target.stat().st_size > MAX_FILE_CHARS * 4:
                raise _err("Файл слишком большой", "The file is too large")
            text = target.read_text(encoding="utf-8")
        if len(text) > MAX_FILE_CHARS:
            raise _err("Файл слишком большой", "The file is too large")
        if mode == "append":
            addition = self.sub(step["append"])
            new_text = text + ("" if text.endswith("\n") or not text else "\n") + addition
        elif mode == "prepend":
            addition = self.sub(step["append"])
            new_text = addition + ("" if addition.endswith("\n") or not text else "\n") + text
        elif mode == "overwrite":
            new_text = self.sub(step["append"])
        elif mode == "regex":
            new_text = re.sub(self.sub(step["find"])[:300], self.sub(step["replace"]), text)
        else:
            find, replace = self.sub(step["find"]), self.sub(step["replace"])
            new_text = text.replace(find, replace) if mode == "replace_all" else text.replace(find, replace, 1)
        if len(new_text) > MAX_FILE_CHARS:
            raise _err("Файл стал бы слишком большим", "The file would become too large")
        changed = new_text != text or not target.exists()
        if changed:
            self._write_text(target, new_text)
        return self._ok(step, started, rel + ("" if changed else " (без изменений / no change)"), text=rel)

    def _do_file_read(self, step: Dict[str, Any], started: float) -> bool:
        rel = self.sub(step["path"])
        target = _resolve_path(self.base, rel)
        if not target.is_file():
            if step.get("missing_ok"):
                self._assign(step["name"], "" if step.get("as") == "text" else [] if step.get("as") == "lines" else {})
                return self._ok(step, started, rel + self.tr(" (нет файла)", " (no file)"), text="")
            raise _err(f"Файл «{rel[:80]}» не найден", f"The file “{rel[:80]}” was not found")
        limit = int(step.get("max_chars") or 100_000)
        with open(target, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read(limit + 1)
        truncated = len(text) > limit
        text = text[:limit].lstrip("\ufeff")
        value: Any = text
        if step.get("as") == "lines":
            value = [line for line in text.splitlines()][:MAX_LIST_ITEMS]
        elif step.get("as") == "json":
            try:
                value = json.loads(text) if text.strip() else {}
            except ValueError:
                raise _err("В файле не JSON", "The file is not JSON") from None
        self._assign(step["name"], value)
        return self._ok(step, started, f"{rel} → {step['name']}" + (" (…)" if truncated else ""),
                        text=text[:2000], output=text[:2000])

    def _do_file_info(self, step: Dict[str, Any], started: float) -> bool:
        rel = self.sub(step["path"])
        target = _resolve_path(self.base, rel, allow_root=True)
        info: Dict[str, Any] = {"exists": target.exists(), "is_file": target.is_file(), "is_dir": target.is_dir(),
                                "name": target.name, "path": rel, "size": 0, "modified": "", "age_minutes": None}
        if target.exists():
            stat = target.stat()
            info["size"] = stat.st_size if target.is_file() else 0
            info["modified"] = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            info["age_minutes"] = _tidy_number(round((time.time() - stat.st_mtime) / 60, 1))
            if target.is_file() and stat.st_size <= 2_000_000:
                try:
                    with open(target, "rb") as handle:
                        info["lines"] = handle.read().count(b"\n")
                except OSError:
                    pass
            if target.is_dir():
                info["count"] = sum(1 for _ in zip(range(10_000), target.iterdir()))
        self._assign(step["name"], info)
        flag = "true" if info["exists"] else "false"
        return self._ok(step, started, f"{rel}: {flag}", text=flag, output=info)

    def _do_file_list(self, step: Dict[str, Any], started: float) -> bool:
        folder = _resolve_path(self.base, self.sub(step.get("path") or "."), allow_root=True)
        if not folder.is_dir():
            raise _err("Папка не найдена", "The folder was not found")
        pattern = self.sub(step.get("pattern") or "*") or "*"
        iterator = folder.rglob("*") if step.get("recursive") else folder.iterdir()
        rows: List[Tuple[Path, Any]] = []
        root = self.base.resolve()
        for index, path in enumerate(iterator):
            if index > 20_000:
                break
            name = path.name
            if name.endswith(".memuai-scenario.tmp") or not fnmatch.fnmatch(name.lower(), pattern.lower()):
                continue
            try:
                resolved = path.resolve()
                if resolved != root and root not in resolved.parents:
                    continue
                if path.is_dir() and not step.get("dirs"):
                    continue
                stat = path.stat()
            except OSError:
                continue
            key = {"modified": -stat.st_mtime, "size": -stat.st_size}.get(step.get("sort", "name"), name.lower())
            rows.append((path, key))
        rows.sort(key=lambda row: row[1])
        files = [self.rel(path) for path, _ in rows[: int(step.get("limit") or 100)]]
        self._assign(step["name"], files)
        return self._ok(step, started, f"{len(files)} → {step['name']}", text="\n".join(files)[:4000], output=files)

    def _do_file_copy(self, step: Dict[str, Any], started: float) -> bool:
        return self._copy_move(step, started, move=False)

    def _do_file_move(self, step: Dict[str, Any], started: float) -> bool:
        return self._copy_move(step, started, move=True)

    def _copy_move(self, step: Dict[str, Any], started: float, *, move: bool) -> bool:
        src_rel, dst_rel = self.sub(step["path"]), self.sub(step["to"])
        src = _resolve_path(self.base, src_rel)
        dst = _resolve_path(self.base, dst_rel)
        if not src.exists():
            raise _err(f"Нет файла «{src_rel[:80]}»", f"No file “{src_rel[:80]}”")
        if src.is_dir() and not move:
            raise _err("Копировать можно только файл (для папки — шаг «Архив ZIP»)",
                       "Only a file can be copied (use “ZIP archive” for a folder)")
        if dst.is_dir():
            dst = dst / src.name
        if dst == src or src in dst.parents:
            raise _err("Нельзя положить файл сам в себя", "A file cannot be placed into itself")
        if dst.exists() and not step.get("overwrite"):
            return self._fail(step, started, dst.name, "Файл назначения уже есть / The destination already exists")
        dst.parent.mkdir(parents=True, exist_ok=True)
        if move:
            if dst.exists() and dst.is_file():
                dst.unlink()
            shutil.move(str(src), str(dst))
        else:
            if src.stat().st_size > 512 * 1024 * 1024:
                raise _err("Файл больше 512 МБ", "The file is larger than 512 MB")
            shutil.copy2(src, dst)
        rel = self.rel(dst)
        verb = self.tr("Перемещено", "Moved") if move else self.tr("Скопировано", "Copied")
        return self._ok(step, started, f"{src_rel} → {rel}", text=rel, line=f"{verb}: {src_rel} → {rel}")

    def _do_file_delete(self, step: Dict[str, Any], started: float) -> bool:
        rel = self.sub(step["path"])
        target = _resolve_path(self.base, rel)
        if not target.exists():
            if step.get("missing_ok", True):
                return self._ok(step, started, rel + self.tr(" (уже нет)", " (already gone)"))
            raise _err("Файл не найден", "The file was not found")
        if target.is_dir():
            try:
                target.rmdir()
            except OSError:
                raise _err("Удалить можно только файл или пустую папку", "Only a file or an empty folder can be deleted") from None
        else:
            target.unlink()
        return self._ok(step, started, rel, text=rel, line=self.tr(f"Удалено: {rel}", f"Deleted: {rel}"))

    def _do_folder_create(self, step: Dict[str, Any], started: float) -> bool:
        rel = self.sub(step["path"])
        target = _resolve_path(self.base, rel)
        if target.exists() and not target.is_dir():
            raise _err("Там уже есть файл с таким именем", "A file with this name already exists there")
        target.mkdir(parents=True, exist_ok=True)
        return self._ok(step, started, rel, text=rel)

    def _do_csv_append(self, step: Dict[str, Any], started: float) -> bool:
        rel = self.sub(step["path"])
        target = _resolve_path(self.base, rel)
        delimiter = "\t" if step.get("delimiter") == "tab" else step.get("delimiter") or ";"
        values = [v.strip() for v in self.sub(step["values"]).split("|")]
        header = [h.strip() for h in self.sub(step.get("header") or "").split("|") if h.strip()]
        fresh = not target.exists() or target.stat().st_size == 0
        if not fresh and target.stat().st_size > MAX_FILE_CHARS * 20:
            raise _err("CSV-файл слишком большой", "The CSV file is too large")
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\n")
        if fresh and header:
            writer.writerow(header)
        writer.writerow(values)
        target.parent.mkdir(parents=True, exist_ok=True)
        if fresh:
            with open(target, "w", encoding="utf-8-sig", newline="") as handle:
                handle.write(buffer.getvalue())
        else:
            with open(target, "rb") as handle:
                handle.seek(-1, os.SEEK_END)
                needs_newline = handle.read(1) not in (b"\n", b"\r")
            with open(target, "a", encoding="utf-8", newline="") as handle:
                handle.write(("\n" if needs_newline else "") + buffer.getvalue())
        return self._ok(step, started, rel, text=delimiter.join(values))

    def _do_zip(self, step: Dict[str, Any], started: float) -> bool:
        src_rel, dst_rel = self.sub(step.get("path") or "."), self.sub(step["to"])
        src = _resolve_path(self.base, src_rel, allow_root=True)
        if not dst_rel.lower().endswith(".zip"):
            dst_rel += ".zip"
        dst = _resolve_path(self.base, dst_rel)
        if not src.exists():
            raise _err("Нечего архивировать — путь не найден", "Nothing to archive — the path was not found")
        if dst.exists() and not step.get("overwrite", True):
            return self._fail(step, started, dst.name, "Архив уже есть / The archive already exists")
        files = [src] if src.is_file() else [p for p in src.rglob("*") if p.is_file()]
        total, count = 0, 0
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".memuai-scenario.tmp")
        root = self.base.resolve()
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                resolved = path.resolve()
                if resolved == dst.resolve() or path.name.endswith(".memuai-scenario.tmp") or root not in resolved.parents:
                    continue
                size = path.stat().st_size
                total += size
                count += 1
                if count > MAX_ZIP_FILES or total > MAX_ZIP_BYTES:
                    archive.close()
                    tmp.unlink(missing_ok=True)
                    raise _err("Слишком много данных для архива (до 2000 файлов и 200 МБ)",
                               "Too much data for the archive (up to 2000 files and 200 MB)")
                arcname = path.name if src.is_file() else path.relative_to(src).as_posix()
                archive.write(path, arcname)
        os.replace(tmp, dst)
        rel = self.rel(dst)
        return self._ok(step, started, f"{count} → {rel}", text=rel,
                        line=self.tr(f"Архив: {rel} ({count} файлов)", f"Archive: {rel} ({count} files)"))

    # ------------------------------------------------------------- логика
    async def _repeat(self, step: Dict[str, Any], depth: int, started: float) -> bool:
        limit = step["times"] or MAX_REPEAT
        result = True
        for index in range(limit):
            self.variables["loop"] = {"index": index, "count": limit, "number": index + 1}
            if "while" in step and not _cond_holds(step["while"], self._vars()):
                break
            if not await self.run_steps(step["steps"], depth + 1):
                result = False
                break
            if self.executed >= MAX_EXECUTED:
                result = False
                break
        self.variables.pop("loop", None)
        self.last = {"ok": result, "text": "", "output": ""}
        self._note(step, result, self._ms(started), f"repeat {limit}")
        return result

    async def _foreach(self, step: Dict[str, Any], depth: int, started: float) -> bool:
        items = _items_of(self.raw(step["items"]))[: int(step.get("limit") or 50)]
        name = step.get("as") or "item"
        result, done = True, 0
        for index, item in enumerate(items):
            self.variables[name] = item
            self.variables["loop"] = {"index": index, "count": len(items), "number": index + 1,
                                      "first": index == 0, "last": index == len(items) - 1}
            if not await self.run_steps(step["steps"], depth + 1):
                result = False
                break
            done += 1
            if self.executed >= MAX_EXECUTED:
                result = False
                break
        self.variables.pop("loop", None)
        self.last = {"ok": result, "text": str(done), "output": str(done)}
        self._note(step, result, self._ms(started), f"{done}/{len(items)}")
        return result

    async def _try(self, step: Dict[str, Any], depth: int, started: float) -> bool:
        self.error = ""
        ok = await self.run_steps(step["steps"], depth + 1)
        if ok:
            self._note(step, True, self._ms(started), "try")
            return True
        message = self.error or self.tr("ошибка шага", "a step failed")
        self.variables["error"] = message
        result = await self.run_steps(step.get("catch") or [], depth + 1) if step.get("catch") else True
        self.last = {"ok": result, "text": message, "output": message}
        self._note(step, result, self._ms(started), self.tr("перехвачено: ", "caught: ") + message[:120])
        return result

    async def _macro_call(self, step: Dict[str, Any], depth: int, started: float) -> bool:
        macro_id = self.sub(step["macro_id"])
        if macro_id in self.macro_stack or len(self.macro_stack) >= MAX_DEPTH:
            return self._fail(step, started, macro_id, "Макрос вызывает сам себя / The macro calls itself")
        loader = self.macro_loader
        if loader is None:
            from .plan_vars import get_macro as loader  # type: ignore[no-redef]
        macro = loader(macro_id)
        if not macro:
            return self._fail(step, started, macro_id, "Макрос не найден в библиотеке / The macro is not in the library")
        steps = normalize_steps(macro.get("steps") or [])
        if needs_confirmation(steps) and not self.confirmed and not self.dry:
            return self._fail(step, started, macro_id,
                              "Макрос пишет файлы или запускает программы — подтвердите сценарий / "
                              "The macro writes files or runs programs — confirm the scenario")
        self.macro_stack.append(macro_id)
        try:
            result = await self.run_steps(steps, depth + 1)
        finally:
            self.macro_stack.pop()
        self._note(step, result, self._ms(started), str(macro.get("name") or macro_id))
        return result

    async def _do_wait(self, step: Dict[str, Any], started: float) -> bool:
        await asyncio.sleep(int(step.get("seconds") or 1))
        return self._ok(step, started, f"{step.get('seconds')} s")

    def _do_log(self, step: Dict[str, Any], started: float) -> bool:
        text = self.sub(step["text"])
        return self._ok(step, started, text[:300], text=text)

    def _do_notify(self, step: Dict[str, Any], started: float) -> bool:
        text = self.sub(step["text"])
        return self._ok(step, started, text[:120], text=text, output=text, line=text)

    # ------------------------------------------------------------- система
    async def _do_run(self, step: Dict[str, Any], started: float) -> bool:
        argv = [self.sub(a) for a in step["argv"]]
        if not check_run_allowed(argv, self.allowlist):
            return self._fail(step, started, argv[0], "Программа не в белом списке / The program is not in the allowlist")
        cwd = self.base
        if step.get("cwd"):
            cwd = _resolve_path(self.base, self.sub(step["cwd"]), allow_root=True)
            if not cwd.is_dir():
                raise _err("Рабочая папка не найдена", "The working folder was not found")
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=str(cwd), stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT, stdin=asyncio.subprocess.DEVNULL)
        except OSError as exc:
            return self._fail(step, started, argv[0], str(exc)[:200])
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=step["timeout"])
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            return self._fail(step, started, argv[0], "Превышен таймаут / Timeout exceeded")
        output = out.decode("utf-8", "replace")[:MAX_OUTPUT_CHARS]
        ok = proc.returncode == 0
        self.variables["last_exit"] = proc.returncode or 0
        if step.get("name"):
            self._assign(step["name"], output.strip())
        self.last = {"ok": ok, "text": output, "output": output}
        if output.strip() and not step.get("name"):
            self.lines.append(output.strip()[:2000])
        if not ok:
            self.error = f"Код завершения {proc.returncode} / Exit code {proc.returncode}"
        self._note(step, ok, self._ms(started), argv[0], "" if ok else self.error)
        return ok

    def _do_system(self, step: Dict[str, Any], started: float) -> bool:
        metric = step["metric"]
        value: Any
        if metric in ("hostname", "os", "user", "time"):
            import platform
            import getpass
            value = {"hostname": platform.node(), "os": f"{platform.system()} {platform.release()}".strip(),
                     "user": getpass.getuser(), "time": datetime.now().strftime("%H:%M")}[metric]
        else:
            try:
                import psutil
            except ImportError:
                raise _err("Показатель недоступен: нет модуля psutil", "The metric is unavailable: psutil is missing") from None
            if metric == "cpu_pct":
                value = psutil.cpu_percent(interval=0.3)
            elif metric == "ram_pct":
                value = psutil.virtual_memory().percent
            elif metric == "ram_free_gb":
                value = round(psutil.virtual_memory().available / 1024 ** 3, 2)
            elif metric in ("disk_free_gb", "disk_free_pct"):
                usage = shutil.disk_usage(str(self.base))
                value = round(usage.free / 1024 ** 3, 2) if metric == "disk_free_gb" else round(usage.free / usage.total * 100, 1)
            elif metric in ("battery_pct", "plugged"):
                battery = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
                if battery is None:
                    value = "" if metric == "battery_pct" else True
                else:
                    value = round(battery.percent) if metric == "battery_pct" else bool(battery.power_plugged)
            elif metric == "uptime_hours":
                value = round((time.time() - psutil.boot_time()) / 3600, 1)
            elif metric == "processes":
                value = len(psutil.pids())
            else:  # process
                wanted = self.sub(step.get("arg", "")).strip().lower()
                value = False
                for proc in psutil.process_iter(["name"]):
                    name = str(proc.info.get("name") or "").lower()
                    if name == wanted or name == wanted + ".exe" or name.rsplit(".", 1)[0] == wanted:
                        value = True
                        break
        value = _tidy_number(value) if isinstance(value, float) else value
        self._assign(step["name"], value)
        return self._ok(step, started, f"{metric} = {_plain(value, 60)}", text=_plain(value), output=_plain(value))

    async def _do_open_url(self, step: Dict[str, Any], started: float) -> bool:
        url = self.sub(step["url"]).strip()
        if not re.match(r"^https?://[^\s]+$", url, re.I):
            raise _err("Открыть можно только адрес http(s)://", "Only an http(s):// address can be opened")
        import webbrowser
        opened = await asyncio.to_thread(webbrowser.open, url)
        if not opened:
            return self._fail(step, started, url[:80], "Браузер не открылся / The browser did not open")
        return self._ok(step, started, url[:120], text=url)

    # ------------------------------------------------------------- сервисы
    async def _do_integration(self, step: Dict[str, Any], started: float) -> bool:
        args = {k: self.sub(v) if isinstance(v, str) else v for k, v in (step.get("args") or {}).items()}
        runner = self.integration_runner or _default_integration_runner
        ok, text, _extra = await runner(step["tool"], args)
        text = str(text or "")
        if step.get("name"):
            self._assign(step["name"], text)
        self.last = {"ok": ok, "text": text, "output": text}
        if text.strip() and not step.get("name"):
            self.lines.append(text.strip()[:2000])
        if not ok:
            self.error = text[:300]
        self._note(step, ok, self._ms(started), step["tool"], "" if ok else text[:200])
        return ok

    async def _do_http(self, step: Dict[str, Any], started: float) -> bool:
        url = self.sub(step["url"]).strip()
        headers = {k: self.sub(v) for k, v in (step.get("headers") or {}).items()}
        body: Any = self.sub(step.get("body") or "")
        method = step.get("method", "GET")
        if body and method not in READONLY_HTTP and body.strip()[:1] in "[{":
            try:
                body = json.loads(body)
            except ValueError:
                pass
        try:
            response = await self.services.http(method, url, headers, body if method not in READONLY_HTTP else None,
                                                 float(step.get("timeout") or 20))
        except Exception as exc:  # noqa: BLE001 - StepFailed / httpx
            message = str(exc) or type(exc).__name__
            return self._fail(step, started, url[:80], message[:300])
        status = int(response.get("status") or 0)
        text = str(response.get("text") or "")
        parsed: Any = text[:MAX_VALUE_CHARS]
        mode = step.get("parse", "auto")
        if mode == "json" or (mode == "auto" and text.strip()[:1] in "[{"):
            try:
                parsed = json.loads(text)
            except ValueError:
                if mode == "json":
                    return self._fail(step, started, url[:80], self.tr("Ответ не JSON", "The response is not JSON"))
        try:
            self._assign(step["name"], parsed)
        except ScenarioError:
            self._assign(step["name"], text[:MAX_VALUE_CHARS])
        self.variables[step["name"] + "_status"] = status
        ok = 200 <= status < 400
        self.last = {"ok": ok, "text": text[:MAX_OUTPUT_CHARS], "output": text[:MAX_OUTPUT_CHARS], "status": status}
        if not ok:
            self.error = f"HTTP {status}"
        self._note(step, ok, self._ms(started), f"{method} {url[:80]} → {status}", "" if ok else f"HTTP {status}")
        return ok

    async def _do_telegram(self, step: Dict[str, Any], started: float) -> bool:
        text = self.sub(step["text"])
        await self.services.telegram(text, silent=bool(step.get("silent")))
        return self._ok(step, started, text[:120], text=text)

    async def _do_alert(self, step: Dict[str, Any], started: float) -> bool:
        title, text = self.sub(step.get("title") or ""), self.sub(step["text"])
        done = await self.services.notify(title or str(self.task.get("name") or "MemuAI"), text,
                                          windows=bool(step.get("windows", True)), inbox=bool(step.get("inbox", True)),
                                          task=self.task)
        return self._ok(step, started, f"{text[:80]} → {', '.join(done or []) or '—'}", text=text)

    async def _do_ai(self, step: Dict[str, Any], started: float) -> bool:
        model = self.sub(step.get("model") or "").strip() or str(self.services.default_model() or "")
        if not model:
            raise _err("Выберите модель для шага или модель по умолчанию в настройках",
                       "Choose a model for the step or a default model in the settings")
        system = self.sub(step.get("system") or "")
        prompt = self.sub(step["prompt"])
        if step.get("json"):
            system = (system + "\n" if system else "") + "Answer with valid JSON only, no prose, no code fences."
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        answer = str(await self.services.complete(model, messages) or "").strip()
        value: Any = answer
        if step.get("json"):
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", answer.strip())
            try:
                value = json.loads(cleaned)
            except ValueError:
                return self._fail(step, started, model, self.tr("Модель ответила не JSON", "The model did not answer with JSON"))
        self._assign(step["name"], value, step.get("scope", "run"))
        return self._ok(step, started, f"{model} → {step['name']}", text=answer[:4000], output=answer[:4000],
                        tokens=max(1, (len(prompt) + len(system) + len(answer)) // 4))

    async def _do_web_search(self, step: Dict[str, Any], started: float) -> bool:
        query = self.sub(step["query"])
        rows = await self.services.web_search(query, int(step.get("limit") or 5))
        self._assign(step["name"], rows)
        text = "\n".join(f"{r.get('title')} — {r.get('url')}" for r in rows)[:4000]
        return self._ok(step, started, f"{len(rows)} · {query[:60]}", text=text, output=rows)

    def _row_of(self, raw: str) -> List[Dict[str, Any]]:
        value = self.raw(raw)
        if isinstance(value, dict):
            return [value]
        if isinstance(value, list):
            return [v for v in value if isinstance(v, dict)][:500]
        text = str(value or "").strip()
        if text[:1] in "[{":
            try:
                parsed = json.loads(text)
                return [parsed] if isinstance(parsed, dict) else [v for v in parsed if isinstance(v, dict)][:500]
            except ValueError:
                pass
        row: Dict[str, Any] = {}
        for line in text.splitlines():
            if "=" in line:
                key, _, val = line.partition("=")
                if key.strip():
                    row[key.strip()[:60]] = _coerce(val.strip())
        if not row:
            raise _err("Строка MemuBase: JSON или «поле=значение» по строкам", "A MemuBase row: JSON or “field=value” lines")
        return [row]

    async def _do_memubase(self, step: Dict[str, Any], started: float) -> bool:
        from . import flow_engine as fe
        table = self.sub(step["table"]).strip()
        if step["op"] == "insert":
            rows = self._row_of(step["row"])
            box = await asyncio.to_thread(fe.memubase_insert, table, rows, self.sub(step.get("unique") or ""))
            added = int((box or {}).get("added") or 0)
            return self._ok(step, started, f"{table}: +{added}", text=str(added))
        rows = await asyncio.to_thread(fe.memubase_query, table, {}, self.sub(step.get("search") or ""),
                                       int(step.get("limit") or 50))
        if step["op"] == "count":
            self._assign(step["name"], len(rows))
            return self._ok(step, started, f"{table}: {len(rows)}", text=str(len(rows)))
        self._assign(step["name"], rows)
        return self._ok(step, started, f"{table}: {len(rows)}", text=str(len(rows)), output=rows)

    async def _do_memory(self, step: Dict[str, Any], started: float) -> bool:
        from . import memory_v2
        text = self.sub(step["text"]).strip()
        await asyncio.to_thread(memory_v2.add_manual, text, category=self.sub(step.get("category") or ""),
                                service="planning")
        return self._ok(step, started, text[:120], text=text)

    async def _do_task(self, step: Dict[str, Any], started: float) -> bool:
        target = self.sub(step["task_id"]).strip()
        own = str(self.task.get("id") or "")
        op = step["op"]
        if op == "run":
            if target in (own, str(self.task.get("name") or "")) and own:
                raise _err("Задача не может запустить саму себя", "A task cannot run itself")
            depth = _TASK_DEPTH.get()
            if depth >= 2:
                raise _err("Слишком глубокая цепочка запусков задач", "The chain of task runs is too deep")
            token = _TASK_DEPTH.set(depth + 1)
            try:
                ok, text = await self.services.run_task(target)
            finally:
                _TASK_DEPTH.reset(token)
            self.last = {"ok": ok, "text": str(text or ""), "output": str(text or "")}
            self._note(step, ok, self._ms(started), target, "" if ok else str(text or "")[:200])
            if not ok:
                self.error = str(text or "")[:300]
            return ok
        state = await asyncio.to_thread(_set_task_enabled, target, op)
        if state is None:
            raise _err(f"Задача «{target[:60]}» не найдена", f"Task “{target[:60]}” not found")
        word = self.tr("включена", "enabled") if state else self.tr("выключена", "disabled")
        return self._ok(step, started, f"{target}: {word}", text="true" if state else "false")

    async def _do_module(self, step: Dict[str, Any], started: float) -> bool:
        from .module_actions import resolve_action, run_module_action
        module_id, action = self.sub(step["module"]), self.sub(step["action"])
        handler, settings = resolve_action(module_id, action)
        if not handler:
            text = self.tr(f"Действие модуля «{module_id}:{action}» не найдено",
                           f"The module action “{module_id}:{action}” was not found")
            self.last = {"ok": False, "text": text, "output": text}
            self.error = text[:300]
            self._note(step, False, self._ms(started), f"{module_id}:{action}", text[:200])
            return False
        # run_module_action returns a dict {ok, text, files, data}; it never raises.
        result = await run_module_action(module_id, handler, lang=self.lang, task=self.task, settings=settings)
        ok = bool(result.get("ok"))
        text = str(result.get("text") or "")
        files = [str(f) for f in (result.get("files") or [])][:20]
        self.last = {"ok": ok, "text": text, "output": text, "files": files}
        if files:
            self.last["file"] = files[0]
        if text.strip():
            self.lines.append(text.strip()[:3000])
        if not ok:
            self.error = text[:300]
        self._note(step, ok, self._ms(started), f"{module_id}:{handler}", "" if ok else text[:200])
        return ok

    async def _do_command(self, step: Dict[str, Any], started: float) -> bool:
        ok, text, _extra = await self.services.handoff("command", {
            "command": self.sub(step["command"]), "name": self.task.get("name"), "id": self.task.get("id"),
            "lang": self.lang})
        self.last = {"ok": ok, "text": str(text or ""), "output": str(text or "")}
        if not ok:
            self.error = str(text or "")[:300]
        self._note(step, ok, self._ms(started), step["command"], "" if ok else str(text or "")[:200])
        return ok

    async def _do_assistant(self, step: Dict[str, Any], started: float) -> bool:
        params = {k: self.sub(v) if isinstance(v, str) else v for k, v in (step.get("params") or {}).items()}
        ok, text, _extra = await self.services.handoff("action", {
            "command": self.sub(step["action"]), "prompt": json.dumps(params, ensure_ascii=False),
            "name": self.task.get("name"), "id": self.task.get("id"), "lang": self.lang})
        self.last = {"ok": ok, "text": str(text or ""), "output": str(text or "")}
        if not ok:
            self.error = str(text or "")[:300]
        self._note(step, ok, self._ms(started), step["action"], "" if ok else str(text or "")[:200])
        return ok


import contextvars  # noqa: E402

_TASK_DEPTH: "contextvars.ContextVar[int]" = contextvars.ContextVar("memuai_scenario_task_depth", default=0)


def _set_task_enabled(task_id: str, op: str) -> Optional[bool]:
    from .pgdata import _mutate_schedules
    box: Dict[str, Any] = {}

    def update(tasks: List[Dict[str, Any]]) -> bool:
        for task in tasks:
            if str(task.get("id") or "") == task_id or str(task.get("name") or "") == task_id:
                current = task.get("enabled", True) is not False
                new = (not current) if op == "toggle" else op == "enable"
                box["state"] = new
                if current == new:
                    return False
                task["enabled"] = new
                if not new:
                    task.pop("next_run", None)
                return True
        return False

    _mutate_schedules(update)
    if "state" in box:
        try:
            from .schedule_runtime import wake
            wake()
        except Exception:  # noqa: BLE001
            pass
    return box.get("state")


async def _default_integration_runner(tool: str, args: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    """Интеграция через существующий диспетчер (та же проверка, что у kind=integration)."""
    from .schedule_integrations import run as _run
    return await _run({"tool": tool, "args": args})


def _persistable(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool, list, dict)) or value is None


async def execute(scenario: Any, *, base_dir: Any, allowlist: Any = (), confirmed: bool = False,
                  variables: Optional[Dict[str, Any]] = None,
                  integration_runner: Optional[Callable[..., Any]] = None,
                  lang: str = "ru", services: Any = None, task: Optional[Dict[str, Any]] = None,
                  dry: bool = False, task_vars: Optional[Dict[str, Any]] = None,
                  global_vars: Optional[Dict[str, Any]] = None,
                  macros: Optional[Callable[[str], Any]] = None) -> Dict[str, Any]:
    """Выполнить сценарий. Возвращает ``{ok, text, variables, log, steps, persist}`` — не бросает
    исключений за ошибки шагов (они в журнале); ``ScenarioError`` — только за плохой сценарий/базу.

    ``task_vars`` / ``global_vars`` — сохранённые переменные задачи и общие переменные; изменения
    областей ``task`` и ``global`` возвращаются в ``persist`` (их сохраняет планировщик)."""
    clean = normalize_scenario(scenario)
    base = Path(str(base_dir or "")).expanduser()
    if not base.is_dir():
        raise _err("Папка сценария не найдена", "The scenario folder was not found")
    initial = dict(clean["variables"])
    for source in (task_vars, variables):
        if isinstance(source, dict):
            for key, value in source.items():
                if _NAME_RE.match(str(key)) and _persistable(value) and value is not None:
                    initial[str(key)] = value
    runner = _Runner(base_dir=base, allowlist=allowlist, confirmed=confirmed, variables=initial,
                     integration_runner=integration_runner, lang=lang, services=services, task=task, dry=dry,
                     global_vars={k: v for k, v in (global_vars or {}).items() if _NAME_RE.match(str(k))},
                     macros=macros)
    stop_text = ""
    try:
        ok = await runner.run_steps(clean["steps"], 0)
    except _Stop as stop:
        ok, stop_text = stop.ok, stop.text
    lines = [line for line in runner.lines if line]
    if stop_text:
        lines.append(stop_text)
    text = "\n".join(lines).strip()
    if not text:
        text = ("Сценарий выполнен" if lang != "en" else "Scenario completed") if ok else \
            ("Сценарий не выполнен" if lang != "en" else "Scenario failed")
        if not ok and runner.error:
            text += f": {runner.error}"
    persist: Dict[str, Any] = {"task": {}, "global": {}, "deleted_task": [], "deleted_global": []}
    if not dry:
        for scope in ("task", "global"):
            source = runner.global_vars if scope == "global" else runner.variables
            for name in sorted(runner.dirty[scope]):
                if name in source and _persistable(source[name]):
                    persist[scope][name] = source[name]
            persist["deleted_" + scope] = sorted(runner.deleted[scope])
    out_vars = {k: v for k, v in runner.variables.items() if k not in ("loop",)}
    return {"ok": ok, "text": text[:MAX_TEXT], "variables": out_vars, "log": runner.log,
            "steps": runner.executed, "persist": persist, "dry": dry, "error": runner.error}


def execute_sync(scenario: Any, **kwargs: Any) -> Dict[str, Any]:
    """Синхронная обёртка для тестов и простых вызовов (внутри — свой event loop)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(execute(scenario, **kwargs))
    raise RuntimeError("execute_sync нельзя вызывать из работающего event loop / "
                       "execute_sync cannot run inside a running event loop")
