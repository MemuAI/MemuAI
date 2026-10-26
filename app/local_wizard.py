"""The local model wizard (Round 33): which model, which quant, how fast — for this computer.

Inputs: the hardware profile (``local_hw.detect`` or the user's edit of it) and the user's goals
(uses, language, speed vs quality, context, licence, memory to leave free). Output: ranked
recommendations with a concrete GGUF file, memory placement (all in VRAM / split / CPU),
estimated tokens per second, the recommended context, licence and languages, and a short
explanation "why" — plus the models that were ruled out and the reason.

The model list is a data file, ``app/local_catalog/models.json`` (curated facts and task scores;
file names, sizes and KV-cache sizes refreshed from the Hugging Face API). Update path:

* developers run ``python tools/update_local_catalog.py`` (re-reads every repository, writes the
  bundled file; add or edit entries in the JSON first);
* users press «Обновить данные» in the wizard: the same refresh runs inside the Proxy and its
  result is kept in ``DATA_DIR/local-models/recommendations.json`` (used while it is newer than
  the bundled file).

Installation goes through ``local_models``: the exact file is resolved live from the repository,
then ``start_download(..., preset=)`` registers the model with the wizard's settings.
"""
from __future__ import annotations

import json
import math
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import local_hw

GB = 1024 ** 3
MB = 1024 ** 2
HF = "https://huggingface.co"
def _bundled_path() -> Path:
    base = getattr(sys, "_MEIPASS", "")
    if base and (Path(base) / "app" / "local_catalog" / "models.json").is_file():
        return Path(base) / "app" / "local_catalog" / "models.json"
    return Path(__file__).resolve().parent / "local_catalog" / "models.json"

BUNDLED = _bundled_path()

USES = ("chat", "coding", "ru", "vision", "reasoning", "agent", "docs")

QUANT_QUALITY = {"Q2_K": 0.86, "Q3_K_M": 0.93, "Q3_K_XL": 0.94, "Q4_0": 0.955, "Q4_K_S": 0.96, "Q4_K_M": 0.97,
                 "Q4_K_XL": 0.975, "Q5_K_M": 0.985, "Q5_K_XL": 0.987, "Q6_K": 0.995, "Q6_K_XL": 0.996,
                 "Q8_0": 1.0, "Q8_K_XL": 1.0, "MXFP4": 1.0, "BF16": 1.0, "F16": 1.0}
QUANT_NOTE = {
    "Q3_K_M": ("сильное сжатие: заметная, но терпимая потеря качества", "strong compression: a noticeable but tolerable quality loss"),
    "Q3_K_XL": ("сильное сжатие (динамическое): умеренная потеря качества", "strong (dynamic) compression: a moderate quality loss"),
    "Q4_K_M": ("золотая середина: ~97 % качества оригинала при четверти размера", "the sweet spot: ~97% of the original quality at a quarter of the size"),
    "Q4_K_XL": ("динамическое Q4: качество чуть выше обычного Q4_K_M", "dynamic Q4: slightly better than a plain Q4_K_M"),
    "Q5_K_M": ("почти без потерь (~98,5 %)", "nearly lossless (~98.5%)"),
    "Q5_K_XL": ("почти без потерь (динамическое Q5)", "nearly lossless (dynamic Q5)"),
    "Q6_K": ("практически неотличимо от оригинала", "practically indistinguishable from the original"),
    "Q6_K_XL": ("практически неотличимо от оригинала", "practically indistinguishable from the original"),
    "Q8_0": ("без потерь качества, но вдвое больше Q4", "no quality loss, but twice the size of Q4"),
    "Q8_K_XL": ("без потерь качества", "no quality loss"),
    "MXFP4": ("родной формат модели от OpenAI — без потерь", "the model's native OpenAI format — lossless"),
}
USE_LABEL = {
    "chat": ("общение", "conversation"), "coding": ("код", "coding"), "ru": ("русский язык", "Russian"),
    "vision": ("картинки", "images"), "reasoning": ("рассуждения", "reasoning"), "agent": ("агенты и инструменты", "agents and tools"),
    "docs": ("длинные документы", "long documents"),
}
OPEN_LICENSES = {"apache-2.0", "mit"}
LICENSE_LABEL = {"apache-2.0": "Apache 2.0", "mit": "MIT", "llama3.1": "Llama 3.1 Community", "llama3.2": "Llama 3.2 Community",
                 "nvidia-open-model": "NVIDIA Open Model", "yandexgpt-5-lite": "YandexGPT 5 Lite", "other": "—"}

_catalog_lock = threading.Lock()
_refresh: Dict[str, Any] = {"state": "idle", "done": 0, "total": 0, "error": "", "error_en": "", "finished": 0}

def override_path() -> Path:
    from .local_models import root_dir
    return root_dir() / "recommendations.json"

def _read(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        return None
    return data

def load_catalog() -> Dict[str, Any]:
    """The bundled catalogue, or the user's refreshed copy while it is newer."""
    bundled = _read(BUNDLED) or {"schema": 1, "version": "", "models": []}
    try:
        refreshed = _read(override_path())
    except Exception:
        refreshed = None
    if refreshed and str(refreshed.get("version") or "") >= str(bundled.get("version") or "") \
            and int(refreshed.get("schema") or 0) == int(bundled.get("schema") or 0):

        ids = {row.get("id") for row in refreshed["models"]}
        refreshed["models"] += [row for row in bundled["models"] if row.get("id") not in ids]
        refreshed["origin"] = "refreshed"
        return refreshed
    bundled["origin"] = "bundled"
    return bundled

def kv_from_config(config: Dict[str, Any]) -> Dict[str, int]:
    """KV cache size (f16) from a transformers config.json: bytes per context token + a fixed part.

    ``per_token`` — layers with full attention (they grow with the context); ``fixed`` — sliding
    window layers (they hold at most the window). Hybrid layouts (Qwen3.5+ linear attention,
    Nemotron-H Mamba) only count their attention layers; MLA models count the latent cache.
    """
    text = config.get("text_config") if isinstance(config.get("text_config"), dict) else config
    for nested in ("llm_config", "language_config"):
        if isinstance(text.get(nested), dict):
            text = text[nested]
    layers = int(text.get("num_hidden_layers") or text.get("n_layer") or text.get("num_layers") or 0)
    if not layers:
        return {"per_token": 0, "fixed": 0}
    if int(text.get("kv_lora_rank") or 0):
        rope = int(text.get("qk_rope_head_dim") or 64)
        return {"per_token": layers * (int(text["kv_lora_rank"]) + rope) * 2, "fixed": 0}
    heads = int(text.get("num_attention_heads") or 0)
    kv_heads = int(text.get("num_key_value_heads") or heads or 0)
    head_dim = int(text.get("head_dim") or ((int(text.get("hidden_size") or 0) // heads) if heads else 0))
    if not kv_heads or not head_dim:
        return {"per_token": 0, "fixed": 0}
    kinds = text.get("layer_types") or text.get("layers_block_type")
    pattern = text.get("hybrid_override_pattern")
    full, sliding = layers, 0
    if isinstance(kinds, list) and kinds:
        full = sum(1 for kind in kinds if str(kind) in ("full_attention", "attention", "global_attention"))
        sliding = sum(1 for kind in kinds if "sliding" in str(kind) or str(kind) == "local_attention")
    elif isinstance(pattern, str) and pattern:
        full = pattern.count("*")
    elif int(text.get("full_attention_interval") or 0) > 1:
        full = layers // int(text["full_attention_interval"])
    global_heads = int(text.get("num_global_key_value_heads") or kv_heads)
    global_dim = int(text.get("global_head_dim") or head_dim)
    per_token = full * global_heads * global_dim * 2 * 2
    window = int(text.get("sliding_window") or 0)
    fixed = sliding * kv_heads * head_dim * 2 * 2 * window if window else sliding * kv_heads * head_dim * 2 * 2 * 4096
    return {"per_token": int(per_token), "fixed": int(fixed)}

_SHARD_RE = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$", re.I)
_DRAFT_RE = re.compile(r"(?i)(?:^|[-_./])(?:draft|mtp|nextn|eagle3?|dflash)(?:[-_.]|$)")
_FALLBACK_QUANT = {"Q3_K_M": ("Q3_K_XL",), "Q4_K_M": ("Q4_K_XL",), "Q5_K_M": ("Q5_K_XL",), "Q6_K": ("Q6_K_XL",),
                   "Q8_0": ("Q8_K_XL",), "MXFP4": ("F16",)}

def pick_files(tree: List[Dict[str, Any]], quants: List[str]) -> Dict[str, Any]:
    """From a repository listing: one file group per wanted quant, the best projector and MTP draft."""
    from .local_models import quant_from_name
    groups: Dict[str, Dict[str, Any]] = {}
    projectors, drafts = [], []
    for row in tree:
        path = str(row.get("path") or "")
        if row.get("type") != "file" or not path.lower().endswith(".gguf"):
            continue
        size = int((row.get("lfs") or {}).get("size") or row.get("size") or 0)
        name = path.split("/")[-1]
        if "mmproj" in name.lower():
            projectors.append({"path": path, "size": size})
            continue
        if re.search(r"(?i)imatrix", name):
            continue
        if _DRAFT_RE.search(Path(path).stem) or path.lower().startswith("mtp/"):

            if "mtp" in path.lower():
                drafts.append({"path": path, "size": size, "quant": quant_from_name(name)})
            continue
        key = _SHARD_RE.sub(".gguf", path)
        group = groups.setdefault(key, {"files": [], "size": 0, "quant": quant_from_name(key.split("/")[-1]),
                                        "dynamic": "UD-" in name.upper()})
        group["files"].append(path)
        group["size"] += size
    out: Dict[str, Any] = {}
    for quant in quants:
        options = []
        for wanted in (quant,) + _FALLBACK_QUANT.get(quant, ()):
            options = [g for g in groups.values() if g["quant"] == wanted]
            if options:
                break
        if not options:
            continue
        options.sort(key=lambda g: (g["dynamic"], len(g["files"]), g["size"]))
        best = options[0]
        out[quant] = {"files": sorted(best["files"]), "size": best["size"], "quant": best["quant"]}
    def projector_rank(row: Dict[str, Any]) -> Tuple[int, int]:
        name = row["path"].upper()

        order = 1 if "BF16" in name else 0 if "F16" in name else 2 if "Q8_0" in name else 3 if "F32" in name else 9
        return order, row["size"]
    mmproj = sorted(projectors, key=projector_rank)[0] if projectors else None
    draft = None
    if drafts:
        draft = sorted(drafts, key=lambda row: (row["quant"] not in ("Q8_0", "Q4_0"), row["quant"] != "Q8_0", row["size"]))[0]
    return {"files": out, "mmproj": mmproj, "draft": draft}

def refresh_entry(entry: Dict[str, Any], get_json: Callable[[str], Any]) -> Dict[str, Any]:
    """Re-read one model from Hugging Face: files and sizes, projector, MTP draft, KV size, params, context."""
    repo = entry["repo"]
    tree = get_json(f"{HF}/api/models/{repo}/tree/main?recursive=true")
    if not isinstance(tree, list):
        raise ValueError(f"{repo}: unexpected tree listing")
    picked = pick_files(tree, list(entry.get("quants") or []))
    if not picked["files"]:
        raise ValueError(f"{repo}: none of the quants {entry.get('quants')} found")
    updated = dict(entry)
    updated["files"] = picked["files"]
    updated["mmproj"] = picked["mmproj"] if entry.get("caps", {}).get("vision") else None
    updated["draft"] = picked["draft"]
    try:
        info = get_json(f"{HF}/api/models/{repo}?expand[]=gguf&expand[]=gated&expand[]=downloads")
        gguf = info.get("gguf") or {}
        if gguf.get("total"):
            updated["params"] = int(gguf["total"])
        if gguf.get("context_length"):
            updated["ctx_max"] = int(gguf["context_length"])
        if gguf.get("architecture"):
            updated["arch"] = str(gguf["architecture"])
        updated["gated"] = bool(info.get("gated"))
        updated["downloads"] = int(info.get("downloads") or 0)
    except Exception:
        pass
    base = entry.get("base")
    if base:
        try:
            config = get_json(f"{HF}/{base}/resolve/main/config.json")
            if isinstance(config, dict):
                kv = kv_from_config(config)
                if kv["per_token"] or kv["fixed"]:
                    updated["kv"] = kv
                text = config.get("text_config") if isinstance(config.get("text_config"), dict) else config
                experts = int(text.get("num_experts") or text.get("n_routed_experts") or text.get("num_local_experts") or 0)
                updated["moe"] = experts > 1
        except Exception:
            pass
    updated["checked"] = time.strftime("%Y-%m-%d")
    return updated

def refresh_catalog(catalog: Dict[str, Any], get_json: Callable[[str], Any],
                    progress: Optional[Callable[[int, int], None]] = None) -> Tuple[Dict[str, Any], List[str]]:
    """Refresh every entry; an entry that fails keeps its previous data. Returns (catalog, errors)."""
    models, errors = [], []
    total = len(catalog.get("models") or [])
    for index, entry in enumerate(catalog.get("models") or []):
        try:
            models.append(refresh_entry(entry, get_json))
        except Exception as exc:
            errors.append(f"{entry.get('id')}: {type(exc).__name__}: {exc}")
            models.append(entry)
        if progress:
            progress(index + 1, total)
    out = dict(catalog)
    out["models"] = models
    out["version"] = time.strftime("%Y-%m-%d")
    out.pop("origin", None)
    return out, errors

def start_refresh() -> Dict[str, Any]:
    """Refresh the catalogue inside the Proxy (background thread); progress in ``refresh_status``."""
    with _catalog_lock:
        if _refresh["state"] == "running":
            return dict(_refresh)
        _refresh.update({"state": "running", "done": 0, "total": 0, "error": "", "error_en": ""})
    threading.Thread(target=_run_refresh, name="local-catalog-refresh", daemon=True).start()
    return dict(_refresh)

def refresh_status() -> Dict[str, Any]:
    return dict(_refresh)

def _run_refresh() -> None:
    from . import local_models
    try:
        with local_models._client(timeout=30.0) as client:
            headers = local_models._hf_headers()

            def get_json(url: str) -> Any:
                response = client.get(url, headers=headers)
                if response.status_code != 200:
                    raise ValueError(f"HTTP {response.status_code}")
                return response.json()

            def progress(done: int, total: int) -> None:
                _refresh.update({"done": done, "total": total})

            catalog = load_catalog()
            fresh, errors = refresh_catalog(catalog, get_json, progress)
        if len(errors) == len(fresh["models"]):
            raise RuntimeError(errors[0] if errors else "no models")
        target = override_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(fresh, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(target)
        _refresh.update({"state": "done", "finished": int(time.time()), "failed": len(errors)})
    except Exception as exc:
        text = f"{type(exc).__name__}: {exc}"
        _refresh.update({"state": "error", "error": "Не удалось обновить данные каталога: " + text,
                         "error_en": "Could not refresh the catalogue data: " + text})

def _params(entry: Dict[str, Any]) -> float:
    return float(entry.get("params") or 0) / 1e9 or float(entry.get("params_b") or 0)

def active_fraction(entry: Dict[str, Any]) -> float:
    """Share of the weights read per token: 1 for dense models, active/total for MoE."""
    total = _params(entry)
    active = float(entry.get("active_b") or 0)
    if not active:
        match = re.search(r"(?i)\bA(\d+(?:\.\d+)?)B\b", f"{entry.get('name', '')} {entry.get('id', '')}".replace("-", " "))
        active = float(match.group(1)) if match else 0
    if active and total and active < total:
        return max(0.04, active / total)
    return 1.0

def kv_bytes(entry: Dict[str, Any], context: int) -> int:
    kv = entry.get("kv") or {}
    per_token = int(kv.get("per_token") or 0)
    fixed = int(kv.get("fixed") or 0)
    if not per_token and not fixed:

        per_token = int(64 * 1024 * max(0.5, math.sqrt(max(1.0, _params(entry)) / 8)))
    return per_token * context + fixed

def budgets(profile: Dict[str, Any], keep_free_gb: float = 0) -> Dict[str, Any]:
    """Memory the model may use: VRAM of the main GPU and RAM, both with honest reserves."""
    ram_total = int((profile.get("ram") or {}).get("total") or 0)
    gpu = profile.get("main_gpu")
    vram = int(gpu.get("vram") or 0) if gpu else 0

    vram_budget = max(0, int(vram * 0.92 - 0.4 * GB)) if vram >= GB else 0

    reserve = max(3.5 * GB, ram_total * 0.2) + max(0.0, float(keep_free_gb or 0)) * GB
    ram_budget = max(0, int(ram_total - reserve))
    return {"vram": vram_budget, "ram": ram_budget, "vram_total": vram, "ram_total": ram_total,
            "gpu_bw": int(gpu.get("bandwidth") or 0) if gpu else 0,
            "ram_bw": int((profile.get("ram") or {}).get("bandwidth") or 50)}

def place(need: int, budget: Dict[str, Any]) -> Tuple[str, float]:
    """(placement, share of the model on the GPU): gpu | partial | cpu | none."""
    if budget["vram"] and need <= budget["vram"]:
        return "gpu", 1.0
    if budget["vram"] and need <= budget["vram"] + budget["ram"]:
        return "partial", budget["vram"] / need
    if need <= budget["ram"]:
        return "cpu", 0.0
    return "none", 0.0

def speed(entry: Dict[str, Any], size: int, context: int, placement: str, share: float,
          profile: Dict[str, Any], budget: Dict[str, Any]) -> float:
    """Estimated generation speed (tokens/s) — memory bandwidth bound, CPU compute capped."""
    if placement == "none" or not size:
        return 0.0
    fraction = active_fraction(entry)
    read = size * fraction + kv_bytes(entry, context // 2) * 0.5

    efficiency = 0.78 if fraction >= 1 else 0.45
    gpu_bw = budget["gpu_bw"] * 1e9 * efficiency
    cpu_bw = budget["ram_bw"] * 1e9 * (0.9 if fraction >= 1 else 0.6)
    if placement == "gpu" and gpu_bw:
        seconds = read / gpu_bw + 0.0025
    elif placement == "partial" and gpu_bw:
        seconds = share * read / gpu_bw + (1 - share) * read / cpu_bw + 0.004
    else:
        seconds = read / cpu_bw + 0.004
    tps = 1 / seconds if seconds > 0 else 0.0
    cpu = profile.get("cpu") or {}
    if placement != "gpu":
        cores = max(1, int(cpu.get("cores") or 4))
        flops_per_cycle = 32 if cpu.get("avx512") else 16 if cpu.get("avx2") is not False else 8
        flops = cores * flops_per_cycle * 3.4e9 * 0.3
        active_params = max(0.3, _params(entry) * active_fraction(entry)) * 1e9
        cpu_share = 1.0 if placement == "cpu" else (1 - share)
        tps = min(tps, flops / (2 * active_params * max(0.2, cpu_share)))
    return round(min(tps, 400.0), 1)

def speed_score(tps: float) -> float:
    """0 at 2 tokens/s or less, 100 at 50 or more, logarithmic in between."""
    if tps <= 2:
        return 0.0
    return max(0.0, min(100.0, 100 * math.log(tps / 2) / math.log(25)))

DEFAULT_GOALS: Dict[str, Any] = {
    "uses": ["chat"], "language": "ru", "priority": 40, "context": 0, "license": "any",
    "keep_free_gb": 0, "limit": 8,
}

def normalize_goals(goals: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    goals = goals if isinstance(goals, dict) else {}
    out = dict(DEFAULT_GOALS)
    uses = [str(use) for use in goals.get("uses") or [] if str(use) in USES]
    out["uses"] = list(dict.fromkeys(uses)) or ["chat"]
    out["language"] = goals.get("language") if goals.get("language") in ("ru", "en", "both") else "ru"
    try:
        out["priority"] = max(0, min(100, int(goals.get("priority", 40))))
    except (TypeError, ValueError):
        out["priority"] = 40
    try:
        context = int(goals.get("context") or 0)
    except (TypeError, ValueError):
        context = 0
    if not context:
        context = 65536 if "docs" in out["uses"] else 32768 if {"coding", "agent"} & set(out["uses"]) else 16384
    out["context"] = max(2048, min(1_048_576, context))
    out["license"] = goals.get("license") if goals.get("license") in ("any", "commercial", "open") else "any"
    try:
        out["keep_free_gb"] = max(0.0, min(64.0, float(goals.get("keep_free_gb") or 0)))
    except (TypeError, ValueError):
        out["keep_free_gb"] = 0.0
    try:
        out["limit"] = max(1, min(30, int(goals.get("limit") or 8)))
    except (TypeError, ValueError):
        out["limit"] = 8
    out["mtp"] = bool(goals.get("mtp"))
    return out

def _weights(goals: Dict[str, Any]) -> Dict[str, float]:
    uses = set(goals["uses"])
    weights = {"chat": 1.0 if "chat" in uses or not uses - {"ru", "docs"} else 0.35}
    for use, weight in (("coding", 1.3), ("vision", 1.2), ("reasoning", 1.15), ("agent", 1.2)):
        if use in uses:
            weights[use] = weight
    if "docs" in uses:
        weights["chat"] = max(weights["chat"], 0.8)
        weights["reasoning"] = max(weights.get("reasoning", 0), 0.6)
    language = goals["language"]
    ru = 1.6 if language == "ru" else 0.7 if language == "both" else 0.0
    if "ru" in uses:
        ru = max(ru, 1.8)
    if ru:
        weights["ru"] = ru
    return weights

def quality(entry: Dict[str, Any], goals: Dict[str, Any]) -> float:
    scores = entry.get("scores") or {}
    weights = _weights(goals)
    total = sum(weights.values()) or 1.0
    value = sum(weight * float(scores.get(use) or 0) for use, weight in weights.items()) / total
    if (goals["language"] == "ru" or "ru" in goals["uses"]) and float(scores.get("ru") or 0) < 40:
        value -= 8
    return max(0.0, value)

def _license_ok(entry: Dict[str, Any], rule: str) -> bool:
    if rule == "open":
        return str(entry.get("license") or "") in OPEN_LICENSES
    if rule == "commercial":
        return bool(entry.get("commercial"))
    return True

def _gb(value: float, english: bool) -> str:
    text = f"{value / GB:.1f}"
    return (text if english else text.replace(".", ",")) + (" GB" if english else " ГБ")

def _num(value: float, english: bool, digits: int = 0) -> str:
    text = f"{value:.{digits}f}"
    return text if english else text.replace(".", ",")

def _ctx_label(tokens: int) -> str:
    if tokens >= 1024 * 1024:
        return f"{tokens // (1024 * 1024)}M"
    return f"{tokens // 1024}K" if tokens >= 1024 else str(tokens)

def evaluate(entry: Dict[str, Any], goals: Dict[str, Any], profile: Dict[str, Any],
             budget: Dict[str, Any]) -> Dict[str, Any]:
    """Every quant of one model on this computer; the best is ``best`` (None when nothing fits)."""
    caps = entry.get("caps") or {}
    wants_vision = "vision" in goals["uses"]
    ctx_max = int(entry.get("ctx_max") or 32768)
    desired = min(goals["context"], ctx_max)
    mmproj = int((entry.get("mmproj") or {}).get("size") or 0) if wants_vision and caps.get("vision") else 0
    draft = int((entry.get("draft") or {}).get("size") or 0) if goals.get("mtp") else 0
    p = goals["priority"] / 100
    q_base = quality(entry, goals)
    options = []
    for quant, info in (entry.get("files") or {}).items():
        size = int(info.get("size") or 0)
        if not size:
            continue
        chosen = None
        context = desired
        while context >= 2048:
            need = int(size * 1.02 + mmproj + draft + kv_bytes(entry, context) + 0.5 * GB)
            placement, share = place(need, budget)
            if placement != "none":
                chosen = (context, need, placement, share)
                break
            context //= 2
        if not chosen:
            options.append({"quant": quant, "size": size, "placement": "none", "need": int(size * 1.02 + 0.5 * GB)})
            continue
        context, need, placement, share = chosen
        tps = speed(entry, size, context, placement, share, profile, budget)
        q = q_base * QUANT_QUALITY.get(str(info.get("quant") or quant), 0.95)
        s = speed_score(tps)
        score = q * (1 - 0.6 * p) + s * 0.6 * p
        minimum = 3 + 12 * p
        if tps < minimum:
            score *= 0.55 + 0.45 * (tps / minimum)
        if placement == "gpu":
            score += 2
        if context < desired:
            score -= 6 * math.log2(desired / context)
        options.append({"quant": quant, "file_quant": info.get("quant") or quant, "files": info.get("files") or [],
                        "size": size, "context": context, "need": need, "placement": placement,
                        "gpu_share": round(share, 2), "tps": tps, "quality": round(q, 1), "speed": round(s, 1),
                        "score": round(score, 2)})
    fitting = [option for option in options if option["placement"] != "none"]
    best = max(fitting, key=lambda o: (o["score"], -o["size"])) if fitting else None
    return {"best": best, "options": sorted(options, key=lambda o: o["size"]), "desired": desired,
            "mmproj": mmproj, "draft": draft, "quality": q_base}

def explain(entry: Dict[str, Any], result: Dict[str, Any], goals: Dict[str, Any], profile: Dict[str, Any],
            english: bool) -> Tuple[List[str], List[str]]:
    """(reasons, warnings) for a recommendation in one language."""
    L = (lambda ru, en: en) if english else (lambda ru, en: ru)
    best = result["best"]
    reasons: List[str] = [str((entry.get("why") or {}).get("en" if english else "ru") or "")]
    gpu = profile.get("main_gpu") or {}
    tps = _num(best["tps"], english, 0 if best["tps"] >= 10 else 1)
    if best["placement"] == "gpu":
        reasons.append(L(f"Целиком помещается в видеопамять ({gpu.get('name', 'GPU')}): ≈ {tps} токенов/с.",
                         f"Fits entirely in video memory ({gpu.get('name', 'GPU')}): ≈ {tps} tokens/s."))
    elif best["placement"] == "partial":
        share = int(round(best["gpu_share"] * 100))
        reasons.append(L(f"≈ {share} % модели на видеокарте, остальное в ОЗУ: ≈ {tps} токенов/с.",
                         f"≈ {share}% of the model on the GPU, the rest in RAM: ≈ {tps} tokens/s."))
    else:
        reasons.append(L(f"Работает на процессоре из ОЗУ: ≈ {tps} токенов/с.", f"Runs on the CPU from RAM: ≈ {tps} tokens/s."))
    scores = entry.get("scores") or {}
    strong = [use for use in goals["uses"] if use in scores and float(scores.get(use) or 0) >= 70]
    if strong:
        labels = ", ".join(USE_LABEL[use][1 if english else 0] for use in strong)
        reasons.append(L(f"Сильна в том, что вы выбрали: {labels}.", f"Strong at what you picked: {labels}."))
    if goals["language"] in ("ru", "both") or "ru" in goals["uses"]:
        ru = float(scores.get("ru") or 0)
        if ru >= 75:
            reasons.append(L("Отлично пишет по-русски.", "Writes Russian very well."))
        elif ru >= 60:
            reasons.append(L("Хорошо пишет по-русски.", "Writes Russian well."))
    quant = str(best.get("file_quant") or best["quant"])
    note = QUANT_NOTE.get(quant)
    if note:
        reasons.append(L(f"Квантование {quant} — {note[0]}.", f"Quantisation {quant} — {note[1]}."))
    ctx_max = int(entry.get("ctx_max") or 0)
    reasons.append(L(f"Контекст {_ctx_label(best['context'])} токенов (максимум модели — {_ctx_label(ctx_max)}).",
                     f"Context {_ctx_label(best['context'])} tokens (the model's maximum is {_ctx_label(ctx_max)})."))
    fraction = active_fraction(entry)
    if fraction < 1:
        active = _params(entry) * fraction
        reasons.append(L(f"MoE: на каждый токен работают ≈ {_num(active, english, 1)} млрд из {_num(_params(entry), english, 0)} млрд параметров — "
                         "быстрее плотной модели того же размера.",
                         f"MoE: ≈ {_num(active, english, 1)}B of {_num(_params(entry), english, 0)}B parameters work per token — "
                         "faster than a dense model of the same size."))
    if result["mmproj"]:
        reasons.append(L(f"Видит изображения: вместе с моделью скачается проектор ({_gb(result['mmproj'], english)}).",
                         f"Sees images: the projector is downloaded with the model ({_gb(result['mmproj'], english)})."))
    license_id = str(entry.get("license") or "")
    label = LICENSE_LABEL.get(license_id, license_id or "—")
    if entry.get("commercial"):
        reasons.append(L(f"Лицензия {label}: можно использовать и в работе.", f"Licence {label}: commercial use is allowed."))
    warnings: List[str] = []
    if not entry.get("commercial"):
        warnings.append(L(f"Лицензия {label} ограничивает коммерческое использование — прочитайте её перед работой.",
                          f"The {label} licence restricts commercial use — read it before using it for work."))
    if best["context"] < result["desired"]:
        warnings.append(L(f"Чтобы поместиться в память, контекст уменьшен до {_ctx_label(best['context'])}.",
                          f"To fit in memory the context is reduced to {_ctx_label(best['context'])}."))
    if best["placement"] == "partial":
        warnings.append(L("Часть слоёв в ОЗУ — генерация медленнее, чем целиком на видеокарте.",
                          "Some layers stay in RAM — generation is slower than fully on the GPU."))
    if (goals["language"] == "ru" or "ru" in goals["uses"]) and float(scores.get("ru") or 0) < 45:
        warnings.append(L("Русский язык у этой модели слабый — лучше общаться по-английски.",
                          "This model's Russian is weak — English works better."))
    if "vision" in goals["uses"] and not (entry.get("caps") or {}).get("vision"):
        warnings.append(L("Не видит изображения.", "Cannot see images."))
    if {"agent", "coding"} & set(goals["uses"]) and not (entry.get("caps") or {}).get("tools"):
        warnings.append(L("Не умеет вызывать инструменты — для агентов не подойдёт.", "No tool calling — not for agents."))
    budget = budgets(profile, goals["keep_free_gb"])
    ram_part = best["need"] - (budget["vram"] if best["placement"] == "partial" else 0)
    if best["placement"] in ("partial", "cpu") and budget["ram"] and ram_part > 0.85 * budget["ram"]:
        warnings.append(L("Займёт почти всю свободную память — закройте тяжёлые программы перед запуском.",
                          "Takes almost all free memory — close heavy programs before starting it."))
    if best["tps"] < 5:
        warnings.append(L("Скорость низкая: ответ будет печататься медленно.", "Slow: answers will appear slowly."))
    disk = int((profile.get("disk") or {}).get("free") or 0)
    download = best["size"] + result["mmproj"] + result["draft"]
    if disk and download > disk - 2 * GB:
        warnings.append(L(f"Мало места на диске: нужно {_gb(download, english)}, свободно {_gb(disk, english)}.",
                          f"Low disk space: {_gb(download, english)} needed, {_gb(disk, english)} free."))
    if entry.get("gated"):
        warnings.append(L("Закрытый репозиторий: нужен токен Hugging Face и принятая лицензия.",
                          "Gated repository: needs a Hugging Face token and an accepted licence."))
    return [reason for reason in reasons if reason], warnings

def preset_for(entry: Dict[str, Any], best: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    """Model settings the wizard applies after installation."""
    sampling = entry.get("sampling") or {}
    cores = int((profile.get("cpu") or {}).get("cores") or 0)
    preset: Dict[str, Any] = {"name": entry.get("name") or "", "context": int(best["context"]), "gpu_layers": -1,
                              "threads": cores if best["placement"] != "gpu" and cores else 0}
    for key in ("temperature", "top_p", "top_k", "min_p", "repeat_penalty"):
        if sampling.get(key) is not None:
            preset[key] = sampling[key]
    return preset

def installed_index() -> Dict[str, Dict[str, Any]]:
    """Installed local models by repository (to mark recommendations already on the disk)."""
    try:
        from . import local_models
        rows = local_models.list_models()
    except Exception:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if row.get("repo"):
            out.setdefault(str(row["repo"]).lower(), row)
    return out

def recommend(goals: Optional[Dict[str, Any]] = None, profile: Optional[Dict[str, Any]] = None,
              catalog: Optional[Dict[str, Any]] = None, installed: Optional[Dict[str, Dict[str, Any]]] = None
              ) -> Dict[str, Any]:
    """Ranked recommendations for these goals on this hardware."""
    goals = normalize_goals(goals)
    profile = local_hw.finish(profile) if profile is not None else local_hw.detect()
    catalog = catalog or load_catalog()
    installed = installed if installed is not None else installed_index()
    budget = budgets(profile, goals["keep_free_gb"])
    uses = set(goals["uses"])
    results, excluded = [], []
    for entry in catalog.get("models") or []:
        caps = entry.get("caps") or {}
        name = str(entry.get("name") or entry.get("id"))

        def drop(ru: str, en: str) -> None:
            excluded.append({"id": entry.get("id"), "name": name, "reason": ru, "reason_en": en})

        if "vision" in uses and not caps.get("vision"):
            drop("не видит изображения", "cannot see images")
            continue
        if "agent" in uses and not caps.get("tools"):
            drop("не умеет вызывать инструменты", "no tool calling")
            continue
        if not _license_ok(entry, goals["license"]):
            drop("лицензия не подходит под выбранное условие", "the licence does not meet your condition")
            continue
        if goals["context"] > 2 * int(entry.get("ctx_max") or 0) and int(entry.get("ctx_max") or 0) < 65536:
            drop(f"контекст модели всего {_ctx_label(int(entry.get('ctx_max') or 0))}",
                 f"the model's context is only {_ctx_label(int(entry.get('ctx_max') or 0))}")
            continue
        result = evaluate(entry, goals, profile, budget)
        best = result["best"]
        if not best:
            smallest = min((o["need"] for o in result["options"]), default=0)
            drop(f"не помещается в память (нужно от {_gb(smallest, False)})",
                 f"does not fit in memory (needs {_gb(smallest, True)} or more)")
            continue
        reasons_ru, warnings_ru = explain(entry, result, goals, profile, False)
        reasons_en, warnings_en = explain(entry, result, goals, profile, True)
        have = installed.get(str(entry.get("repo") or "").lower())
        tps = best["tps"]
        results.append({
            "id": entry.get("id"), "name": name, "vendor": entry.get("vendor", ""), "family": entry.get("family", ""),
            "repo": entry.get("repo"), "released": entry.get("released", ""),
            "params_b": round(_params(entry), 1), "active_b": round(_params(entry) * active_fraction(entry), 1),
            "moe": active_fraction(entry) < 1,
            "quant": best["quant"], "file_quant": best["file_quant"], "files": best["files"], "size": best["size"],
            "mmproj": entry.get("mmproj") if result["mmproj"] else None,
            "draft": entry.get("draft"), "download": best["size"] + result["mmproj"] + result["draft"],
            "placement": best["placement"], "gpu_share": best["gpu_share"], "need": best["need"],
            "tps": tps, "tps_range": [round(tps * 0.75, 1), round(tps * 1.25, 1)],
            "context": best["context"], "ctx_max": int(entry.get("ctx_max") or 0),
            "quality": best["quality"], "speed": best["speed"], "score": best["score"],
            "license": entry.get("license", ""), "license_label": LICENSE_LABEL.get(str(entry.get("license") or ""), entry.get("license", "")),
            "commercial": bool(entry.get("commercial")), "languages": entry.get("languages") or [],
            "caps": caps, "scores": entry.get("scores") or {},
            "why": reasons_ru, "why_en": reasons_en, "warnings": warnings_ru, "warnings_en": warnings_en,
            "preset": preset_for(entry, best, profile),
            "alternatives": [{key: option.get(key) for key in ("quant", "file_quant", "size", "placement", "tps", "context", "score", "need")}
                             for option in result["options"]],
            "installed": bool(have), "installed_id": (have or {}).get("id", ""),
            "hf_url": f"{HF}/{entry.get('repo')}",
        })
    results.sort(key=lambda row: -row["score"])
    for rank, row in enumerate(results, 1):
        row["rank"] = rank
    top = results[:goals["limit"]]
    return {"goals": goals, "hardware": profile, "budget": budget, "results": top,
            "more": results[goals["limit"]:], "excluded": excluded,
            "catalog": {"version": catalog.get("version", ""), "origin": catalog.get("origin", ""),
                        "count": len(catalog.get("models") or [])},
            "summary": summary(profile, budget, top, english=False), "summary_en": summary(profile, budget, top, english=True)}

def summary(profile: Dict[str, Any], budget: Dict[str, Any], top: List[Dict[str, Any]], english: bool) -> str:
    L = (lambda ru, en: en) if english else (lambda ru, en: ru)
    gpu = profile.get("main_gpu")
    if gpu:
        hw = L(f"видеокарта {gpu['name']} ({_gb(gpu['vram'], english)}) и {_gb(budget['ram_total'], english)} ОЗУ",
               f"a {gpu['name']} ({_gb(gpu['vram'], english)}) and {_gb(budget['ram_total'], english)} of RAM")
    else:
        hw = L(f"без отдельной видеокарты, {_gb(budget['ram_total'], english)} ОЗУ",
               f"no dedicated GPU, {_gb(budget['ram_total'], english)} of RAM")
    if not top:
        return L(f"На этом компьютере ({hw}) под выбранные условия ничего не подошло — ослабьте требования.",
                 f"Nothing matches your conditions on this computer ({hw}) — relax the requirements.")
    first = top[0]
    return L(f"Для этого компьютера ({hw}) лучший выбор — {first['name']} {first['file_quant']}: ≈ {_num(first['tps'], english)} токенов/с.",
             f"For this computer ({hw}) the best pick is {first['name']} {first['file_quant']}: ≈ {_num(first['tps'], english)} tokens/s.")

def find_entry(model_id: str) -> Optional[Dict[str, Any]]:
    return next((row for row in load_catalog().get("models") or [] if row.get("id") == model_id), None)

def install(model_id: str, quant: str, context: int = 0, vision: bool = True, mtp: bool = False,
            profile: Optional[Dict[str, Any]] = None, preset: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One click: the engine (when missing, Windows), the exact files of the chosen quant and the settings.

    The file names are resolved live from the repository (they may have been renamed since the
    catalogue was made); the catalogue's names are the fallback when Hugging Face cannot be listed.
    """
    from . import local_models
    entry = find_entry(str(model_id or ""))
    if not entry:
        raise local_models.LocalModelError("Такой модели нет в каталоге мастера", "This model is not in the wizard catalogue")
    files = (entry.get("files") or {}).get(quant)
    if not files:
        raise local_models.LocalModelError(f"У модели нет кванта {quant}", f"The model has no {quant} quant")
    picked = None
    try:
        listing = local_models.hf_files(entry["repo"])
        tree = [{"type": "file", "path": path, "size": 0} for group in listing.get("files") or [] for path in group["files"]]
        tree += [{"type": "file", "path": row["path"], "size": row.get("size", 0)} for row in listing.get("projectors") or []]
        tree += [{"type": "file", "path": row["path"], "size": row.get("size", 0)} for row in listing.get("drafts") or []]
        picked = pick_files(tree, [quant])
    except local_models.LocalModelError:
        picked = None
    chosen = (picked or {}).get("files", {}).get(quant) if picked else None
    model_files = list((chosen or files).get("files") or [])
    projector = ""
    if vision and (entry.get("caps") or {}).get("vision"):
        projector = str(((picked or {}).get("mmproj") or entry.get("mmproj") or {}).get("path") or "")
    draft = ""
    if mtp:
        draft = str(((picked or {}).get("draft") or entry.get("draft") or {}).get("path") or "")
    settings = dict(preset or {})
    if not settings:
        context = int(context or 0) or min(16384, int(entry.get("ctx_max") or 16384))
        settings = {"name": entry.get("name") or "", "context": context, "gpu_layers": -1}
        for key in ("temperature", "top_p", "top_k", "min_p", "repeat_penalty"):
            if (entry.get("sampling") or {}).get(key) is not None:
                settings[key] = entry["sampling"][key]
    if context:
        settings["context"] = int(context)
    runtime = local_models.runtime_status()
    engine = None
    if not runtime["installed"] and runtime["install"].get("state") != "running":
        try:
            engine = local_models.install_runtime("auto")
        except local_models.LocalModelError as exc:
            engine = {"error": exc.message, "error_en": exc.message_en}
    job = local_models.start_download(entry["repo"], model_files, projector, draft, preset=settings)
    return {"download": job, "engine": engine, "runtime": local_models.runtime_status()}
