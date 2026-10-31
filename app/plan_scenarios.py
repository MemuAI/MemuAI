# -*- coding: utf-8 -*-
"""Программные сценарии планирования: шаги, переменные, файлы, запуск и макросы.

Сценарий — это список шагов, который хранится в задаче планирования
(``kind == "scenario"``, поле ``scenario``) и выполняется планировщиком
без модели: быстро, предсказуемо и с полным журналом. Шаг бывает пяти
видов плюс логика:

* ``set``        — присвоить переменную, прибавить/вычесть число;
* ``file_create`` — создать файл с заданным значением (только внутри базы);
* ``file_edit``   — заменить текст по шаблону или дописать в конец файла;
* ``run``         — запустить программу из белого списка (argv, без shell);
* ``integration`` — вызвать инструмент Telegram Use / Chrome через
  существующий :mod:`app.schedule_integrations` (без новых токенов);
* ``notify``      — добавить строку в текст результата (уведомление);
* ``if`` / ``repeat`` / ``macro`` — условие, ограниченный цикл, группа шагов.

Подстановка ``${var}`` работает в текстах, путях и аргументах. База файлов —
каталог данных пользователя (или папка, явно выбранная им в задаче):
путь за её пределы через ``..`` или симлинк отклоняется. Деструктивный шаг
(запись файла, запуск) выполняется только если при создании правила
пользователь поставил подтверждение (``confirmed``); иначе шаг в журнале
отмечается как «нужно подтверждение» и прогон завершается ошибкой.
Всё выполнение ограничено: не больше 200 шагов, глубина 4, цикл до 20
итераций, файл до 1 МБ, вывод команды до 64 КБ, таймаут команды до 300 с.
"""
from __future__ import annotations

import asyncio
import math
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

MAX_STEPS = 100
MAX_EXECUTED = 200
MAX_DEPTH = 4
MAX_REPEAT = 20
MAX_VARIABLES = 50
MAX_FILE_CHARS = 1_000_000
MAX_OUTPUT_CHARS = 64_000
MAX_TEXT = 20_000
RUN_TIMEOUT_DEFAULT = 30
RUN_TIMEOUT_MAX = 300

STEP_TYPES = ("set", "file_create", "file_edit", "run", "integration", "notify", "if", "repeat", "macro")
DESTRUCTIVE = ("file_create", "file_edit", "run")
COND_OPS = ("eq", "ne", "gt", "ge", "lt", "le", "contains", "not_contains", "empty", "not_empty")

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}$")
_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_.]*)(?::-([^}]*))?\}")


class ScenarioError(ValueError):
    """Ошибка сценария с русским и английским текстом для пользователя."""

    def __init__(self, ru: str, en: str) -> None:
        super().__init__(ru)
        self.ru, self.en = ru, en

    def text(self, lang: str) -> str:
        return self.en if lang == "en" else self.ru


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


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    return str(value or "")[:limit]


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "да")
    return default if value is None else bool(value)


def _resolve_path(base: Path, rel: Any) -> Path:
    """Путь внутри базы: ``..`` и симлинки наружу отклоняются."""
    raw = str(rel or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise _err("Путь к файлу должен быть относительным, внутри выбранной папки",
                   "The file path must be relative, inside the chosen folder")
    root = base.resolve()
    target = (root / raw).resolve()
    if target != root and root not in target.parents:
        raise _err("Путь выходит за пределы выбранной папки", "The path leaves the chosen folder")
    return target


def resolve_var(name: str, variables: Dict[str, Any]) -> Any:
    """``loop.index`` / ``last.ok`` — точечный путь по переменным."""
    parts = name.split(".")
    value: Any = variables
    for part in parts:
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return None
    return value


def substitute(text: Any, variables: Dict[str, Any]) -> str:
    """``${var}`` и ``${var:-по умолчанию}``; неизвестная переменная — пусто."""
    def repl(match: "re.Match[str]") -> str:
        value = resolve_var(match.group(1), variables)
        if value is None:
            return match.group(2) or ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (dict, list)):
            return _text(value, 500)
        return str(value)
    return _VAR_RE.sub(repl, _text(text, MAX_TEXT * 5))


def _normalize_cond(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise _err("Условие должно быть объектом", "The condition must be an object")
    left = str(raw.get("left") or raw.get("var") or raw.get("name") or "")
    op = str(raw.get("op") or "eq").lower()
    if op not in COND_OPS:
        raise _err(f"Неизвестное сравнение «{op[:20]}»", f"Unknown comparison “{op[:20]}”")
    return {"left": _text(left, 500), "op": op,
            "right": _text(raw.get("right") if "right" in raw else raw.get("value"), 500)}


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
    label = _text(raw.get("label"), 120)
    step: Dict[str, Any] = {"type": kind}
    if label:
        step["label"] = label
    if kind == "set":
        name = str(raw.get("name") or raw.get("var") or "").strip()
        if not _NAME_RE.match(name):
            raise _err("Имя переменной — латиница, цифры и _", "A variable name uses Latin letters, digits and _")
        op = str(raw.get("op") or "set").lower()
        if op not in ("set", "inc", "dec", "add"):
            raise _err(f"Неизвестная операция «{op[:20]}»", f"Unknown operation “{op[:20]}”")
        step.update(name=name, op=op)
        if op == "set":
            step["value"] = _text(raw.get("value"), MAX_TEXT)
        else:
            step["by"] = _int(raw.get("by"), 1, -1_000_000, 1_000_000)
    elif kind == "file_create":
        step.update(path=_text(raw.get("path"), 500), content=_text(raw.get("content"), MAX_FILE_CHARS),
                    overwrite=_bool(raw.get("overwrite"), False),
                    require_confirm=_bool(raw.get("require_confirm"), False))
        if not step["path"].strip():
            raise _err("Укажите путь к файлу", "Enter the file path")
    elif kind == "file_edit":
        mode = str(raw.get("mode") or "replace_first").lower()
        if mode not in ("replace_first", "replace_all", "append"):
            raise _err(f"Неизвестный режим правки «{mode[:20]}»", f"Unknown edit mode “{mode[:20]}”")
        step.update(path=_text(raw.get("path"), 500), mode=mode,
                    find=_text(raw.get("find"), MAX_TEXT), replace=_text(raw.get("replace"), MAX_TEXT),
                    append=_text(raw.get("append") or raw.get("content"), MAX_TEXT),
                    require_confirm=_bool(raw.get("require_confirm"), False))
        if not step["path"].strip():
            raise _err("Укажите путь к файлу", "Enter the file path")
        if mode != "append" and not step["find"]:
            raise _err("Укажите текст для замены", "Enter the text to replace")
    elif kind == "run":
        argv = raw.get("argv") or raw.get("command") or []
        if isinstance(argv, str):
            argv = [argv]
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
            raise _err("Команда — список слов без shell", "The command is a list of words, no shell")
        step.update(argv=[_text(a, 500) for a in argv[:32]],
                    timeout=_int(raw.get("timeout"), RUN_TIMEOUT_DEFAULT, 1, RUN_TIMEOUT_MAX),
                    require_confirm=_bool(raw.get("require_confirm"), True))
    elif kind == "integration":
        from .schedule_integrations import normalize as _norm
        try:
            step.update(_norm(raw.get("tool"), raw.get("args", {})))
        except Exception as exc:  # noqa: BLE001 - текст ошибки уже пользовательский
            raise _err(str(exc), str(exc)) from None
        step["type"] = "integration"
        step["require_confirm"] = _bool(raw.get("require_confirm"), False)
    elif kind == "notify":
        step["text"] = _text(raw.get("text") or raw.get("message"), MAX_TEXT)
        if not step["text"].strip():
            raise _err("Введите текст уведомления", "Enter the notification text")
    elif kind == "if":
        step["cond"] = _normalize_cond(raw.get("cond") or raw.get("if") or {})
        step["then"] = [_normalize_step(s, depth + 1, counter) for s in (raw.get("then") or raw.get("yes") or [])]
        step["else"] = [_normalize_step(s, depth + 1, counter) for s in (raw.get("else") or raw.get("no") or [])]
    elif kind == "repeat":
        cond = raw.get("while") or raw.get("cond")
        step["times"] = _int(raw.get("times"), 0, 0, MAX_REPEAT)
        if cond is not None:
            step["while"] = _normalize_cond(cond)
        if not step["times"] and "while" not in step:
            raise _err("Укажите число повторов или условие", "Set the repeat count or a condition")
        step["steps"] = [_normalize_step(s, depth + 1, counter) for s in (raw.get("steps") or raw.get("do") or [])]
    else:  # macro
        step["steps"] = [_normalize_step(s, depth + 1, counter) for s in (raw.get("steps") or [])]
        if not step["steps"]:
            raise _err("Макрос без шагов", "A macro without steps")
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
            if _NAME_RE.match(str(key)) and isinstance(value, (str, int, float, bool)):
                variables[str(key)] = value
    return {"steps": steps, "variables": variables}


def is_destructive(step: Dict[str, Any]) -> bool:
    return step.get("type") in DESTRUCTIVE


def describe_step(step: Dict[str, Any], lang: str = "ru") -> str:
    en = lang == "en"
    kind = step.get("type")
    label = step.get("label") or ""
    prefix = f"{label}: " if label else ""
    if kind == "set":
        return prefix + (f"Set {step['name']} = {step.get('value', step.get('by'))}" if en
                         else f"Переменная {step['name']} = {step.get('value', step.get('by'))}")
    if kind == "file_create":
        return prefix + (f"Create file {step['path']}" if en else f"Создать файл {step['path']}")
    if kind == "file_edit":
        return prefix + (f"Edit file {step['path']} ({step['mode']})" if en
                         else f"Изменить файл {step['path']} ({step['mode']})")
    if kind == "run":
        return prefix + (f"Run: {' '.join(step['argv'][:2])}" if en else f"Запустить: {' '.join(step['argv'][:2])}")
    if kind == "integration":
        return prefix + (f"Integration: {step.get('tool')}" if en else f"Интеграция: {step.get('tool')}")
    if kind == "notify":
        return prefix + (step.get("text") or "")
    if kind == "if":
        return prefix + ("If…" if en else "Если…")
    if kind == "repeat":
        return prefix + (f"Repeat {step.get('times') or 'while'}×" if en else f"Повторить {step.get('times') or 'пока'}×")
    return prefix + (step.get("name") or "Macro" if en else step.get("name") or "Макрос")


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


def _num(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _cond_holds(cond: Dict[str, Any], variables: Dict[str, Any]) -> bool:
    left_raw = substitute(cond["left"], variables)
    right_raw = substitute(cond["right"], variables)
    op = cond["op"]
    if op == "empty":
        return not left_raw.strip()
    if op == "not_empty":
        return bool(left_raw.strip())
    if op in ("contains", "not_contains"):
        found = right_raw in left_raw
        return found if op == "contains" else not found
    ln, rn = _num(left_raw), _num(right_raw)
    if ln is not None and rn is not None:
        left, right = ln, rn
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


class _Runner:
    def __init__(self, *, base_dir: Path, allowlist: Any, confirmed: bool,
                 variables: Dict[str, Any], integration_runner: Any, lang: str = "ru") -> None:
        self.base = base_dir
        self.allowlist = allowlist
        self.confirmed = confirmed
        self.variables = dict(variables)
        self.integration_runner = integration_runner
        self.lang = lang
        self.log: List[Dict[str, Any]] = []
        self.lines: List[str] = []
        self.executed = 0
        self.last: Dict[str, Any] = {"ok": True, "text": "", "output": ""}

    def _vars(self) -> Dict[str, Any]:
        return {**self.variables, "last": self.last}

    def _note(self, step: Dict[str, Any], ok: bool, ms: float, detail: str = "", error: str = "") -> None:
        entry: Dict[str, Any] = {"type": step.get("type"), "ok": ok, "ms": round(ms, 1),
                                 "detail": detail[:300]}
        if step.get("label"):
            entry["label"] = step["label"]
        if error:
            entry["error"] = error[:300]
        self.log.append(entry)

    async def run_steps(self, steps: List[Dict[str, Any]], depth: int) -> bool:
        ok = True
        for step in steps:
            if self.executed >= MAX_EXECUTED:
                self._note(step, False, 0, "limit", "Превышен лимит шагов / Step limit exceeded")
                return False
            self.executed += 1
            if not await self.run_step(step, depth):
                ok = False
                break
        return ok

    async def run_step(self, step: Dict[str, Any], depth: int) -> bool:
        started = time.monotonic()
        kind = step["type"]
        try:
            if kind == "if":
                branch = step["then"] if _cond_holds(step["cond"], self._vars()) else step["else"]
                result = await self.run_steps(branch, depth + 1)
                self.last = {"ok": result, "text": "", "output": ""}
                self._note(step, result, (time.monotonic() - started) * 1000, "if")
                return result
            if kind == "repeat":
                result = await self._repeat(step, depth, started)
                return result
            if kind == "macro":
                result = await self.run_steps(step["steps"], depth + 1)
                self._note(step, result, (time.monotonic() - started) * 1000, step.get("label") or "macro")
                return result
            if kind == "set":
                return self._set(step, started)
            if kind in DESTRUCTIVE and step.get("require_confirm") and not self.confirmed:
                self._note(step, False, (time.monotonic() - started) * 1000,
                           step.get("path") or " ".join(step.get("argv", [])[:1]),
                           "Нужно подтверждение при создании правила / Confirmation at rule creation is required")
                self.last = {"ok": False, "text": "", "output": ""}
                return False
            if kind == "file_create":
                return self._file_create(step, started)
            if kind == "file_edit":
                return self._file_edit(step, started)
            if kind == "run":
                return await self._run(step, started)
            if kind == "integration":
                return await self._integration(step, started)
            if kind == "notify":
                text = substitute(step["text"], self._vars())
                self.lines.append(text)
                self.last = {"ok": True, "text": text, "output": text}
                self._note(step, True, (time.monotonic() - started) * 1000, text[:120])
                return True
        except ScenarioError as exc:
            self._note(step, False, (time.monotonic() - started) * 1000, "", exc.ru)
            self.last = {"ok": False, "text": "", "output": ""}
            return False
        except (OSError, ValueError) as exc:
            self._note(step, False, (time.monotonic() - started) * 1000, "", f"{type(exc).__name__}: {exc}"[:200])
            self.last = {"ok": False, "text": "", "output": ""}
            return False
        self._note(step, False, (time.monotonic() - started) * 1000, "", "Unknown step")
        return False

    def _set(self, step: Dict[str, Any], started: float) -> bool:
        name, op = step["name"], step["op"]
        if op == "set":
            value: Any = substitute(step["value"], self._vars())
            number = _num(value)
            self.variables[name] = (int(number) if number == int(number) else number) if number is not None and str(value).strip() != "" else value
        else:
            current = _num(self.variables.get(name)) or 0.0
            delta = float(step["by"]) * (1 if op in ("inc", "add") else -1)
            total = current + delta
            self.variables[name] = int(total) if total == int(total) else total
        self.last = {"ok": True, "text": str(self.variables[name]), "output": str(self.variables[name])}
        self._note(step, True, (time.monotonic() - started) * 1000, name)
        return True

    def _file_create(self, step: Dict[str, Any], started: float) -> bool:
        target = _resolve_path(self.base, substitute(step["path"], self._vars()))
        content = substitute(step["content"], self._vars())
        if len(content) > MAX_FILE_CHARS:
            raise _err("Содержимое файла слишком большое", "The file content is too large")
        if target.exists() and not step["overwrite"]:
            self._note(step, False, (time.monotonic() - started) * 1000, str(target.name),
                       "Файл уже существует / The file already exists")
            self.last = {"ok": False, "text": "", "output": ""}
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".memuai-scenario.tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
        self.last = {"ok": True, "text": step["path"], "output": ""}
        self.lines.append(f"Файл создан: {step['path']}" if self.lang != "en" else f"File created: {step['path']}")
        self._note(step, True, (time.monotonic() - started) * 1000, step["path"])
        return True

    def _file_edit(self, step: Dict[str, Any], started: float) -> bool:
        target = _resolve_path(self.base, substitute(step["path"], self._vars()))
        if not target.is_file():
            raise _err("Файл не найден", "The file was not found")
        text = target.read_text(encoding="utf-8")
        if len(text) > MAX_FILE_CHARS:
            raise _err("Файл слишком большой", "The file is too large")
        mode = step["mode"]
        if mode == "append":
            addition = substitute(step["append"], self._vars())
            new_text = text + ("" if text.endswith("\n") or not text else "\n") + addition
            changed = True
        else:
            find = substitute(step["find"], self._vars())
            replace = substitute(step["replace"], self._vars())
            if mode == "replace_all":
                new_text = text.replace(find, replace)
            else:
                new_text = text.replace(find, replace, 1)
            changed = new_text != text
        if changed:
            tmp = target.with_name(target.name + ".memuai-scenario.tmp")
            tmp.write_text(new_text, encoding="utf-8")
            os.replace(tmp, target)
        self.last = {"ok": True, "text": step["path"], "output": ""}
        detail = step["path"] + ("" if changed else " (без изменений / no change)")
        self._note(step, True, (time.monotonic() - started) * 1000, detail)
        return True

    async def _run(self, step: Dict[str, Any], started: float) -> bool:
        argv = [substitute(a, self._vars()) for a in step["argv"]]
        if not check_run_allowed(argv, self.allowlist):
            self._note(step, False, (time.monotonic() - started) * 1000, argv[0],
                       "Программа не в белом списке / The program is not in the allowlist")
            self.last = {"ok": False, "text": "", "output": ""}
            return False
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=str(self.base), stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT, stdin=asyncio.subprocess.DEVNULL)
        except OSError as exc:
            self._note(step, False, (time.monotonic() - started) * 1000, argv[0], str(exc)[:200])
            self.last = {"ok": False, "text": "", "output": ""}
            return False
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=step["timeout"])
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            self._note(step, False, (time.monotonic() - started) * 1000, argv[0],
                       "Превышен таймаут / Timeout exceeded")
            self.last = {"ok": False, "text": "", "output": ""}
            return False
        output = out.decode("utf-8", "replace")[:MAX_OUTPUT_CHARS]
        ok = proc.returncode == 0
        self.variables["last_exit"] = proc.returncode or 0
        self.last = {"ok": ok, "text": output, "output": output}
        if output.strip():
            self.lines.append(output.strip()[:2000])
        self._note(step, ok, (time.monotonic() - started) * 1000, argv[0],
                   "" if ok else f"Код завершения {proc.returncode} / Exit code {proc.returncode}")
        return ok

    async def _integration(self, step: Dict[str, Any], started: float) -> bool:
        args = {k: substitute(v, self._vars()) if isinstance(v, str) else v
                for k, v in (step.get("args") or {}).items()}
        runner = self.integration_runner or _default_integration_runner
        ok, text, _extra = await runner(step["tool"], args)
        self.last = {"ok": ok, "text": text, "output": text}
        if text.strip():
            self.lines.append(text.strip()[:2000])
        self._note(step, ok, (time.monotonic() - started) * 1000, step["tool"],
                   "" if ok else text[:200])
        return ok

    async def _repeat(self, step: Dict[str, Any], depth: int, started: float) -> bool:
        limit = step["times"] or MAX_REPEAT
        result = True
        for index in range(limit):
            self.variables["loop"] = {"index": index, "count": limit}
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
        self._note(step, result, (time.monotonic() - started) * 1000, f"repeat {limit}")
        return result


async def _default_integration_runner(tool: str, args: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    """Интеграция через существующий диспетчер (та же проверка, что у kind=integration)."""
    from .schedule_integrations import run as _run
    return await _run({"tool": tool, "args": args})


async def execute(scenario: Any, *, base_dir: Any, allowlist: Any = (), confirmed: bool = False,
                  variables: Optional[Dict[str, Any]] = None,
                  integration_runner: Optional[Callable[..., Any]] = None,
                  lang: str = "ru") -> Dict[str, Any]:
    """Выполнить нормализованный сценарий. Возвращает ``{ok, text, variables, log}`` — не бросает
    исключений за ошибки шагов (они в журнале); ``ScenarioError`` — только за плохой сценарий/базу."""
    clean = normalize_scenario(scenario)
    base = Path(str(base_dir or "")).expanduser()
    if not base.is_dir():
        raise _err("Папка сценария не найдена", "The scenario folder was not found")
    initial = dict(clean["variables"])
    if isinstance(variables, dict):
        for key, value in variables.items():
            if _NAME_RE.match(str(key)) and isinstance(value, (str, int, float, bool)):
                initial[str(key)] = value
    runner = _Runner(base_dir=base, allowlist=allowlist, confirmed=confirmed,
                     variables=initial, integration_runner=integration_runner, lang=lang)
    ok = await runner.run_steps(clean["steps"], 0)
    text = "\n".join(line for line in runner.lines if line).strip()
    if not text:
        text = ("Сценарий выполнен" if lang != "en" else "Scenario completed") if ok else \
            ("Сценарий не выполнен" if lang != "en" else "Scenario failed")
    return {"ok": ok, "text": text[:MAX_TEXT], "variables": dict(runner.variables),
            "log": runner.log, "steps": runner.executed}


def execute_sync(scenario: Any, **kwargs: Any) -> Dict[str, Any]:
    """Синхронная обёртка для тестов и простых вызовов (внутри — свой event loop)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(execute(scenario, **kwargs))
    raise RuntimeError("execute_sync нельзя вызывать из работающего event loop / "
                       "execute_sync cannot run inside a running event loop")
