"""Validated prices and modalities from public model listings."""
from __future__ import annotations

import math
import re
from typing import Any


def _number(value: Any, multiplier: float = 1) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        result = float(value) * multiplier
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _created(value: Any) -> int | None:
    """Release time of a catalogue row as a Unix timestamp, or None.

    OpenRouter sends ``created`` as Unix seconds; other catalogues may send
    a numeric string or an ISO date. Anything else (missing, negative, far
    future, garbage) is not a date and is dropped, so a bad row can never
    poison the sort. The key is only added when a real date exists, so
    rows without one keep the exact spec shape they always had.
    """
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        stamp = float(value)
    except (TypeError, ValueError):
        try:
            from datetime import datetime
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
            stamp = parsed.timestamp()
        except (TypeError, ValueError, OverflowError):
            return None
    if not math.isfinite(stamp) or stamp <= 0:
        return None
    if stamp >= 1e12:
        stamp /= 1000
    if stamp > 4102444800:
        return None
    return int(stamp)


def listing_spec(row: Any) -> dict:
    if not isinstance(row, dict):
        return {}
    pricing = row.get("pricing") if isinstance(row.get("pricing"), dict) else {}
    arch = row.get("architecture") if isinstance(row.get("architecture"), dict) else {}
    provider = row.get("top_provider") if isinstance(row.get("top_provider"), dict) else {}
    def modes(value):
        return list(dict.fromkeys(v.strip().lower() for v in value
                                 if isinstance(v, str) and re.fullmatch(r"[a-zA-Z_-]{1,40}", v.strip())))[:12] if isinstance(value, list) else []
    result = {"context": int(_number(row.get("context_length")) or _number(provider.get("context_length")) or 0),
              "input": _number(pricing.get("prompt"), 1e6), "output": _number(pricing.get("completion"), 1e6),
              "inputs": modes(arch.get("input_modalities") or row.get("input_modalities")),
              "outputs": modes(arch.get("output_modalities") or row.get("output_modalities"))}
    # Older catalogues expose only a compact "text+image->text" modality.
    if isinstance(arch.get("modality"), str) and "->" in arch["modality"]:
        inputs, outputs = arch["modality"].split("->", 1)
        result["inputs"] = result["inputs"] or modes(inputs.split("+"))
        result["outputs"] = result["outputs"] or modes(outputs.split("+"))
    created = _created(row.get("created"))
    if created:
        result["created"] = created
    return result


def listing_key(value: Any) -> str:
    # Pricing variants (:free, :extended, …) must keep distinct identities.
    return re.sub(r"[^a-z0-9:]+", "", str(value or "").lower().removeprefix("models/").split("/")[-1])


def find_spec(key: str, name: str, specs: dict) -> dict:
    exact = specs.get(str(key).lower())
    if exact is not None:
        return exact
    if "/" in str(key).removeprefix("models/"):
        return {}
    for value in (key, name):
        identity = listing_key(value)
        candidates = [spec for model, spec in specs.items() if identity and listing_key(model) == identity]
        if len(candidates) == 1:
            return candidates[0]
    return {}


def notification_detail(spec: dict, *, en: bool = False) -> str:
    unknown = "not listed" if en else "не указана"
    def price(value):
        value = _number(value)
        return unknown if value is None else ("free" if en else "бесплатно") if value == 0 else "$" + format(value, ".8g")
    modes = {"text": "текст", "image": "изображения", "audio": "аудио", "video": "видео", "embedding": "эмбеддинги", "embeddings": "эмбеддинги", "file": "файлы"}
    outputs = spec.get("outputs") if isinstance(spec.get("outputs"), list) else []
    output = ", ".join(str(v) if en else modes.get(str(v), str(v)) for v in outputs) or ("not listed" if en else "не указан")
    return (("Price / 1M tokens (input → output): " if en else "Цена / 1M токенов (вход → выход): ")
            + price(spec.get("input")) + " → " + price(spec.get("output"))
            + ("; output: " if en else "; выход: ") + output)
