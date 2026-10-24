"""Typed positional Telegram commands; the last text/chat argument consumes the rest."""
from __future__ import annotations

import re
from typing import Any


class ArgumentError(ValueError):
    pass


SUMMARY_ARGUMENTS = [
    {"name": "count", "type": "integer", "required": True, "min": 1, "max": 1000,
     "description": "Количество сообщений / Number of messages"},
    {"name": "chat", "type": "chat", "required": False, "default": "",
     "description": "Название, @username или ID чата / Chat title, @username or ID"},
]


def schema(value: Any) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError("Аргументы: список до 32 полей / Arguments must be a list of up to 32 fields")
    out = []
    for item in value:
        if not isinstance(item, dict) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", str(item.get("name") or "")):
            raise ValueError("Имя аргумента: латинские буквы, цифры и _ / Use letters, digits and _ in argument names")
        name = item["name"]
        if name == "question" or name in {r["name"] for r in out}:
            raise ValueError("Имя аргумента повторяется или зарезервировано / Duplicate or reserved argument name")
        kind = item.get("type", "text")
        if kind not in ("text", "chat", "integer", "number", "boolean", "choice"):
            raise ValueError("Неизвестный тип аргумента / Unknown argument type")
        row = {"name": name, "type": kind, "required": item.get("required") is True,
               "description": str(item.get("description") or "")[:300]}
        for key in ("min", "max"):
            if item.get(key) not in (None, ""):
                try:
                    number = float(item[key])
                    if not (-1e9 <= number <= 1e9):
                        raise ValueError()
                except (TypeError, ValueError, OverflowError):
                    raise ValueError("Граница аргумента должна быть конечным числом / Argument bound must be finite") from None
                row[key] = int(number) if kind == "integer" else number
        if row.get("min", -1e9) > row.get("max", 1e9):
            raise ValueError("Минимум аргумента больше максимума / Argument minimum exceeds maximum")
        if kind == "choice":
            choices = item.get("choices")
            if not isinstance(choices, list) or not choices or len(choices) > 100:
                raise ValueError("Добавьте варианты аргумента / Add argument choices")
            row["choices"] = list(dict.fromkeys(str(v)[:200] for v in choices))
        if "default" in item:
            row["default"] = convert(item["default"], row, allow_empty=not row["required"])
        out.append(row)
    return out


def convert(value: Any, spec: dict, *, allow_empty: bool = False) -> Any:
    text = str(value if value is not None else "").strip()
    if not text and allow_empty:
        return ""
    kind, name = spec["type"], spec["name"]
    try:
        if kind == "integer":
            if not re.fullmatch(r"[+-]?\d+", text):
                raise ValueError()
            result: Any = int(text)
        elif kind == "number":
            result = float(text)
            if not (-1e9 <= result <= 1e9):
                raise ValueError()
        elif kind == "boolean":
            if text.casefold() not in ("true", "false", "yes", "no", "1", "0", "да", "нет"):
                raise ValueError()
            result = text.casefold() in ("true", "yes", "1", "да")
        elif kind == "choice":
            result = next(v for v in spec["choices"] if v.casefold() == text.casefold())
        else:
            result = text[:8000]
            if not result and spec.get("required"):
                raise ValueError()
        if kind in ("number", "integer") and not spec.get("min", -1e9) <= result <= spec.get("max", 1e9):
            raise ValueError()
        return result
    except (ValueError, TypeError, StopIteration, OverflowError):
        detail = f" ({spec.get('min', '…')}–{spec.get('max', '…')})" if "min" in spec or "max" in spec else ""
        raise ArgumentError(f"Проверьте аргумент {name}{detail} / Check argument {name}{detail}") from None


def usage(command: dict) -> str:
    return str(command["trigger"]) + "".join(
        " <" + s["name"] + ">" if s.get("required") else " [" + s["name"] + "]"
        for s in command.get("arguments") or [])


def parse(command: dict, text: str) -> dict:
    specs = command.get("arguments") or []
    if not specs:
        return {"question": text.strip()}
    remaining = text.strip()
    out = {"question": text.strip()}
    for index, spec in enumerate(specs):
        if not remaining:
            if "default" in spec:
                out[spec["name"]] = spec["default"]
            elif not spec.get("required"):
                out[spec["name"]] = ""
            else:
                raise ArgumentError("Используйте / Usage: " + usage(command))
            continue
        if index == len(specs) - 1 and spec["type"] in ("text", "chat"):
            value, remaining = remaining, ""
            if len(value) > 1 and value[0] in "\"'" and value[-1] == value[0]:
                value = value[1:-1]
        else:
            match = re.match(r'''(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'|(\S+))(?:\s+|$)''', remaining)
            if not match:
                raise ArgumentError("Проверьте кавычки / Check quotes. " + usage(command))
            value = next(g for g in match.groups() if g is not None)
            remaining = remaining[match.end():].strip()
        out[spec["name"]] = convert(value, spec, allow_empty=not spec.get("required"))
    if remaining:
        raise ArgumentError("Слишком много аргументов / Too many arguments. " + usage(command))
    return out


def render(prompt: str, values: dict) -> str:
    # A single pass prevents argument contents from becoming template instructions.
    return re.sub(r"\{([A-Za-z][A-Za-z0-9_]*)\}", lambda m: str(values.get(m[1], m[0])), prompt)
