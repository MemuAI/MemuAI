"""Local models for MemuAI Proxy: GGUF files run by llama.cpp.

What lives here:

* the catalogue of installed models and their settings (``catalog.json``);
* a reader for GGUF metadata (architecture, context length, quantisation);
* Hugging Face search, file listing and resumable downloads;
* the llama.cpp runtime installer (``llama-server``), fetched on demand;
* a pool of ``llama-server`` processes: started lazily on the first request,
  limited in number, unloaded after idle time, stopped with the Proxy.

The provider in ``providers/local.py`` turns chat requests into calls to the
OpenAI-compatible endpoint of the matching server process. Nothing here is
imported by the MemuAI chat app; it runs inside MemuAI Proxy only.
"""
from __future__ import annotations

import atexit
import errno
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from collections import deque
from pathlib import Path
from typing import Any, BinaryIO, Deque, Dict, List, Optional, Tuple

from .config import DATA_DIR
from .version import APP_VERSION

HF_BASE = "https://huggingface.co"
GITHUB_REPO = "ggml-org/llama.cpp"
PREFIX = "local"
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,79}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
_SHARD_RE = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$", re.I)
_QUANT_RE = re.compile(r"(?i)(?<![A-Za-z0-9])(IQ\d_[A-Z]+|Q\d_K_[SMLX]L?|Q\d_K|Q\d_\d|Q\d|BF16|F16|F32|MXFP4|TQ\d_\d)(?![A-Za-z0-9])")

_DRAFT_RE = re.compile(r"(?i)(?:^|[-_.])(?:draft|mtp|nextn|eagle3?|dflash|dspark)(?:[-_.]|$)")

DEFAULT_SETTINGS: Dict[str, Any] = {
    "backend": "auto",
    "server_path": "",
    "hf_token": "",
    "max_loaded": 1,
    "threads": 0,
}
MODEL_DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "name": "",
    "context": 8192,
    "gpu_layers": -1,
    "threads": 0,
    "batch": 0,
    "temperature": None,
    "top_p": None,
    "top_k": None,
    "min_p": None,
    "repeat_penalty": None,
    "max_tokens": None,
    "system_prompt": "",
    "idle_minutes": 10,
    "extra_args": "",
    "mmproj": "",
    "draft": "",
}

PRESET_KEYS = frozenset({"name", "context", "gpu_layers", "threads", "batch", "temperature", "top_p", "top_k",
                         "min_p", "repeat_penalty", "max_tokens", "system_prompt", "idle_minutes"})

LAUNCH_KEYS = frozenset({"enabled", "context", "gpu_layers", "threads", "batch", "extra_args", "mmproj", "draft"})

class LocalModelError(Exception):
    """A problem the user can act on: ``str(exc)`` is the Russian text, ``message_en`` the English one.

    The Proxy chat and panel show the variant of the interface language; API clients get English
    (``providers/local.py`` passes both on through ``ProviderError``).
    """

    def __init__(self, message: str, message_en: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.message_en = message_en or message

def error_texts(exc: BaseException) -> Tuple[str, str]:
    """(Russian, English) text of an error for the panel: both from a LocalModelError, else the raw text."""
    if isinstance(exc, LocalModelError):
        return exc.message, exc.message_en
    text = f"{type(exc).__name__}: {exc}"
    return text, text

def root_dir() -> Path:
    return Path(DATA_DIR) / "local-models"

def models_dir() -> Path:
    return root_dir() / "models"

def runtime_dir() -> Path:
    return root_dir() / "runtime"

_catalog_lock = threading.RLock()

def _catalog_path() -> Path:
    return root_dir() / "catalog.json"

def _load_catalog() -> Dict[str, Any]:
    from .storage import load_json_with_recovery
    data = load_json_with_recovery(_catalog_path(), {}, validator=lambda value: isinstance(value, dict))
    data = data if isinstance(data, dict) else {}
    models = data.get("models") if isinstance(data.get("models"), dict) else {}
    settings = dict(DEFAULT_SETTINGS)
    if isinstance(data.get("settings"), dict):
        settings.update({key: data["settings"][key] for key in DEFAULT_SETTINGS if key in data["settings"]})
    runtime = data.get("runtime") if isinstance(data.get("runtime"), dict) else {}
    return {"models": models, "settings": settings, "runtime": runtime}

def _save_catalog(data: Dict[str, Any]) -> None:
    from .storage import save_json
    root_dir().mkdir(parents=True, exist_ok=True)
    save_json(str(_catalog_path()), data, backup=True)

def _mutate(change) -> Dict[str, Any]:
    with _catalog_lock:
        data = _load_catalog()
        change(data)
        _save_catalog(data)
        return data

def slugify(name: str) -> str:
    base = re.sub(r"\.gguf$", "", str(name or ""), flags=re.I)
    base = re.sub(r"-\d{5}-of-\d{5}$", "", base)
    slug = re.sub(r"[^a-z0-9.]+", "-", base.lower()).strip("-.")
    return slug[:72].strip("-.") or "model"

def _unique_slug(base: str, taken: Dict[str, Any]) -> str:
    slug = base
    counter = 2
    while slug in taken:
        slug = f"{base}-{counter}"[:80]
        counter += 1
    return slug

FILE_TYPES = {
    0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1", 10: "Q2_K",
    11: "Q3_K_S", 12: "Q3_K_M", 13: "Q3_K_L", 14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S",
    17: "Q5_K_M", 18: "Q6_K", 19: "IQ2_XXS", 20: "IQ2_XS", 21: "Q2_K_S", 22: "IQ3_XS",
    23: "IQ3_XXS", 24: "IQ1_S", 25: "IQ4_NL", 26: "IQ3_S", 27: "IQ3_M", 28: "IQ2_S",
    29: "IQ2_M", 30: "IQ4_XS", 31: "IQ1_M", 32: "BF16", 36: "TQ1_0", 37: "TQ2_0", 38: "MXFP4",
}
_SCALAR = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}

def _read(stream: BinaryIO, size: int) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise LocalModelError("Файл GGUF обрывается на середине заголовка",
                              "The GGUF file ends in the middle of its header")
    return data

def _read_string(stream: BinaryIO, keep: int = 4096) -> str:
    (length,) = struct.unpack("<Q", _read(stream, 8))
    if length > 64 * 1024 * 1024:
        raise LocalModelError("Повреждённый заголовок GGUF", "Damaged GGUF header")
    if length <= keep:
        return _read(stream, length).decode("utf-8", errors="replace")
    head = _read(stream, keep)
    stream.seek(length - keep, io.SEEK_CUR)
    return head.decode("utf-8", errors="replace")

def _read_value(stream: BinaryIO, kind: int, keep_arrays: bool) -> Any:
    if kind in _SCALAR:
        fmt = _SCALAR[kind]
        return struct.unpack(fmt, _read(stream, struct.calcsize(fmt)))[0]
    if kind == 8:
        return _read_string(stream)
    if kind == 9:
        (inner,) = struct.unpack("<I", _read(stream, 4))
        (count,) = struct.unpack("<Q", _read(stream, 8))
        if inner in _SCALAR and not keep_arrays:
            stream.seek(struct.calcsize(_SCALAR[inner]) * count, io.SEEK_CUR)
            return {"array": inner, "count": count}
        items = []
        for _ in range(count):
            value = _read_value(stream, inner, False)
            if keep_arrays and len(items) < 64:
                items.append(value)
        return items if keep_arrays else {"array": inner, "count": count}
    raise LocalModelError(f"Неизвестный тип значения GGUF: {kind}", f"Unknown GGUF value type: {kind}")

def read_gguf(path: Path) -> Dict[str, Any]:
    """Useful metadata of a GGUF file; tokenizer tables are skipped, not loaded."""
    with open(path, "rb") as stream:
        if _read(stream, 4) != b"GGUF":
            raise LocalModelError("Это не файл GGUF (нет сигнатуры GGUF в начале)",
                                  "This is not a GGUF file (no GGUF signature at the start)")
        (version,) = struct.unpack("<I", _read(stream, 4))
        if version == 1:
            tensors, kv_count = struct.unpack("<II", _read(stream, 8))
        else:
            tensors, kv_count = struct.unpack("<QQ", _read(stream, 16))
        if kv_count > 100_000:
            raise LocalModelError("Повреждённый заголовок GGUF", "Damaged GGUF header")
        meta: Dict[str, Any] = {}
        for _ in range(kv_count):
            key = _read_string(stream, 256)
            (kind,) = struct.unpack("<I", _read(stream, 4))
            value = _read_value(stream, kind, keep_arrays=False)
            if not isinstance(value, dict):
                meta[key] = value
    arch = str(meta.get("general.architecture") or "")
    file_type = meta.get("general.file_type")
    template = str(meta.get("tokenizer.chat_template") or "")
    info = {
        "version": version,
        "tensors": tensors,
        "architecture": arch,
        "name": str(meta.get("general.name") or meta.get("general.basename") or ""),
        "size_label": str(meta.get("general.size_label") or ""),
        "context_length": int(meta.get(f"{arch}.context_length") or 0),
        "layers": int(meta.get(f"{arch}.block_count") or 0),
        "embedding": int(meta.get(f"{arch}.embedding_length") or 0),
        "quant": FILE_TYPES.get(int(file_type), "") if isinstance(file_type, int) else "",
        "chat_template": bool(template),
        "vision": arch in ("clip", "mllama") or bool(meta.get("clip.has_vision_encoder")),
        "license": str(meta.get("general.license") or ""),
        "nextn_layers": int(meta.get(f"{arch}.nextn_predict_layers") or 0),
    }

    heads = _int_meta(meta.get(f"{arch}.attention.head_count"))
    info["head_count_kv"] = _int_meta(meta.get(f"{arch}.attention.head_count_kv")) or heads
    info["key_length"] = _int_meta(meta.get(f"{arch}.attention.key_length")) or (
        info["embedding"] // heads if heads and info["embedding"] else 0)
    info["value_length"] = _int_meta(meta.get(f"{arch}.attention.value_length")) or info["key_length"]
    info["kv_lora_rank"] = _int_meta(meta.get(f"{arch}.attention.kv_lora_rank"))
    info["rope_dims"] = _int_meta(meta.get(f"{arch}.rope.dimension_count"))
    info["full_attention_interval"] = _int_meta(meta.get(f"{arch}.full_attention_interval"))
    info["sliding_window"] = _int_meta(meta.get(f"{arch}.attention.sliding_window"))
    info["draft"] = is_draft_meta(info, path.name if isinstance(path, Path) else str(path))
    return info

def _int_meta(value: Any) -> int:
    try:
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0
    except (TypeError, ValueError, OverflowError):
        return 0

def is_draft_meta(meta: Dict[str, Any], file_name: str = "") -> bool:
    """A speculative-decoding head rather than a model that can run alone.

    Such files keep the metadata of the whole model (65 blocks, the full
    vocabulary) but carry only the few tensors of the draft layer: a real
    model has several tensors per block, a draft head a dozen or two in
    total. llama-server does not reject them — it builds a graph over the
    missing blocks and crashes with an access violation (0xC0000005).
    """
    if not isinstance(meta, dict):
        return False
    if meta.get("draft") is True:
        return True
    layers = int(meta.get("layers") or 0)
    tensors = int(meta.get("tensors") or 0)
    if layers >= 4 and tensors and tensors < layers * 2:
        return True
    return bool(file_name and _DRAFT_RE.search(Path(str(file_name)).stem) and tensors and layers
                and tensors < layers * 4)

def draft_problem(file_name: str, english: bool = False) -> str:
    """The explanation shown whenever a draft head is used as a model."""
    if english:
        return (f"“{file_name}” is not a model but an auxiliary draft file (MTP head) that speeds up the "
                "full model with speculative decoding. It does not run on its own: llama.cpp crashes. "
                "Download the main file from the same repository (usually without the word “draft”, "
                "for example …-Q4_K_M.gguf) and, if you like, set this file in the model settings in "
                "the “Draft model for speed-up” field.")
    return (f"«{file_name}» — это не модель, а вспомогательный draft-файл (MTP-голова) для ускорения "
            "полной модели при спекулятивном декодировании. Сам по себе он не запускается: llama.cpp "
            "аварийно завершается. Скачайте основной файл из того же репозитория (обычно без слова "
            "«draft», например …-Q4_K_M.gguf), а этот файл при желании укажите в настройках модели "
            "в поле «Draft-модель для ускорения».")

def _draft_error(file_name: str) -> "LocalModelError":
    return LocalModelError(draft_problem(file_name), draft_problem(file_name, english=True))

def quant_from_name(name: str) -> str:
    match = _QUANT_RE.search(str(name or ""))
    return match.group(1).upper() if match else ""

_hardware_cache: Dict[str, Any] = {}

def hardware() -> Dict[str, Any]:
    """RAM, GPUs and CPU for the "will it fit" estimate (``app/local_hw.py``; cached there).

    Round 33: exact VRAM from DXGI / nvidia-smi on every system (it was a PowerShell CIM query on
    Windows only, nothing on Linux), free VRAM, physical cores, AVX2 and the suggested backend.
    The old keys (``ram``, ``gpus[{name, vram}]``, ``cpu_threads``) keep their meaning.
    """
    if _hardware_cache:
        return dict(_hardware_cache)
    from . import local_hw
    try:
        info = local_hw.legacy(local_hw.detect(models_path=models_dir()))
    except Exception:
        info = {"ram": 0, "gpus": [], "cpu_threads": os.cpu_count() or 0}
    _hardware_cache.update(info)
    return dict(info)

def kv_bytes_per_token(meta: Optional[Dict[str, Any]]) -> int:
    """KV cache bytes per token of context (f16 cache) from GGUF metadata; 0 when unknown.

    Conservative: sliding-window layers are counted as full ones. Hybrid models that keep a
    KV cache on every N-th layer only (Qwen3.5/3.6/3.8 ``full_attention_interval``) and MLA
    models (DeepSeek-2 style ``kv_lora_rank``) are counted by their real layout.
    """
    if not isinstance(meta, dict):
        return 0
    layers = int(meta.get("layers") or 0)
    if not layers:
        return 0
    if int(meta.get("kv_lora_rank") or 0):
        return layers * (int(meta["kv_lora_rank"]) + int(meta.get("rope_dims") or 64)) * 2
    heads = int(meta.get("head_count_kv") or 0)
    key = int(meta.get("key_length") or 0)
    value = int(meta.get("value_length") or 0) or key
    if not heads or not key:
        return 0
    interval = int(meta.get("full_attention_interval") or 0)
    attention_layers = max(1, layers // interval) if interval > 1 else layers
    return attention_layers * heads * (key + value) * 2

def fit(size_bytes: int, context: int = 8192, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Will a model of this size run here? A rough, honest estimate.

    Levels: ``gpu`` — everything in video memory; ``partial`` — a GPU is there but part of the
    layers stays in RAM (llama.cpp splits them, slower); ``ram`` — no usable GPU, runs from RAM;
    ``tight`` — barely fits; ``too_big``; ``unknown``. Integrated graphics do not count as VRAM.
    """
    hw = hardware()
    per_token = kv_bytes_per_token(meta)
    kv = context * (per_token or 128 * 1024)

    need = int(size_bytes * 1.03 + 600 * 1024 * 1024 + kv)
    vram = max((gpu.get("vram") or 0 for gpu in hw.get("gpus") or [] if not gpu.get("integrated")), default=0)
    ram = hw.get("ram") or 0
    usable_vram = vram * 0.9 if vram >= 1024 ** 3 else 0
    if usable_vram and need <= usable_vram:
        level = "gpu"
    elif usable_vram and ram and need <= usable_vram + ram * 0.7:
        level = "partial"
    elif ram and need <= ram * 0.7:
        level = "ram"
    elif ram and need <= ram * 0.95 + usable_vram:
        level = "tight"
    elif ram:
        level = "too_big"
    else:
        level = "unknown"
    gpu_share = 1.0 if level == "gpu" else (min(1.0, usable_vram / need) if usable_vram and need else 0.0)
    return {"level": level, "need": need, "ram": ram, "vram": vram, "kv": int(kv), "gpu_share": round(gpu_share, 2)}

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

def _client(timeout: float = 30.0):
    import httpx
    from .config import config_store
    settings = config_store.settings
    kwargs: Dict[str, Any] = {"timeout": httpx.Timeout(timeout, read=max(timeout, 60.0)),
                              "follow_redirects": True, "verify": bool(settings.verify_ssl),
                              "headers": {"User-Agent": f"MemuAI-Proxy/{APP_VERSION} (+local models)"}}
    if settings.default_proxy:
        kwargs["proxy"] = settings.default_proxy
    return httpx.Client(**kwargs)

def _hf_headers() -> Dict[str, str]:
    token = str(_load_catalog()["settings"].get("hf_token") or "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}

def hf_search(query: str, sort: str = "downloads", limit: int = 30) -> List[Dict[str, Any]]:
    sort = sort if sort in ("downloads", "likes", "lastModified", "trendingScore") else "downloads"
    params = {"filter": "gguf", "sort": sort, "direction": "-1", "limit": str(max(1, min(60, int(limit or 30))))}
    if str(query or "").strip():
        params["search"] = str(query).strip()[:120]
    with _client() as client:
        response = client.get(f"{HF_BASE}/api/models", params=params, headers=_hf_headers())
    if response.status_code != 200:
        raise LocalModelError(f"Hugging Face ответил HTTP {response.status_code}",
                              f"Hugging Face answered HTTP {response.status_code}")
    rows = response.json() if isinstance(response.json(), list) else []
    out = []
    for row in rows:
        repo = str(row.get("id") or row.get("modelId") or "")
        if not _REPO_RE.match(repo):
            continue
        tags = [str(tag) for tag in row.get("tags") or []]
        out.append({
            "repo": repo,
            "author": repo.split("/")[0],
            "downloads": int(row.get("downloads") or 0),
            "likes": int(row.get("likes") or 0),
            "updated": str(row.get("lastModified") or row.get("createdAt") or ""),
            "pipeline": str(row.get("pipeline_tag") or ""),
            "vision": any(tag in ("image-text-to-text", "vision", "multimodal") for tag in tags)
                      or row.get("pipeline_tag") == "image-text-to-text",
            "gated": bool(row.get("gated")),
            "license": next((tag.split(":", 1)[1] for tag in tags if tag.startswith("license:")), ""),
        })
    return out

def hf_files(repo: str) -> Dict[str, Any]:
    """GGUF files of a repository, split shards grouped into one entry."""
    if not _REPO_RE.match(str(repo or "")):
        raise LocalModelError("Некорректное имя репозитория", "Invalid repository name")
    with _client() as client:
        response = client.get(f"{HF_BASE}/api/models/{repo}/tree/main", params={"recursive": "true"},
                              headers=_hf_headers())
    if response.status_code == 401 or response.status_code == 403:
        raise LocalModelError("Репозиторий закрыт: примите лицензию на сайте Hugging Face и укажите токен в настройках",
                              "The repository is gated: accept its licence on Hugging Face and set a token in the settings")
    if response.status_code == 404:
        raise LocalModelError("Репозиторий не найден", "Repository not found")
    if response.status_code != 200:
        raise LocalModelError(f"Hugging Face ответил HTTP {response.status_code}",
                              f"Hugging Face answered HTTP {response.status_code}")
    groups: Dict[str, Dict[str, Any]] = {}
    projectors = []
    drafts = []
    for row in response.json() if isinstance(response.json(), list) else []:
        path = str(row.get("path") or "")
        if row.get("type") != "file" or not path.lower().endswith(".gguf"):
            continue
        size = int((row.get("lfs") or {}).get("size") or row.get("size") or 0)
        if "mmproj" in path.lower():
            projectors.append({"path": path, "size": size})
            continue
        if re.search(r"(?i)(^|[-_/])imatrix", path):

            continue
        if _DRAFT_RE.search(Path(path).stem):

            drafts.append({"path": path, "size": size, "quant": quant_from_name(path.split("/")[-1])})
            continue
        shard = _SHARD_RE.search(path)
        key = _SHARD_RE.sub(".gguf", path) if shard else path
        group = groups.setdefault(key, {"name": key.split("/")[-1], "files": [], "size": 0,
                                        "quant": quant_from_name(key.split("/")[-1])})
        group["files"].append(path)
        group["size"] += size
    items = []
    for group in groups.values():
        group["files"].sort()
        group["fit"] = fit(group["size"])
        items.append(group)
    items.sort(key=lambda row: row["size"])
    return {"repo": repo, "files": items, "projectors": sorted(projectors, key=lambda row: row["size"]),
            "drafts": sorted(drafts, key=lambda row: row["size"])}

class Download:
    def __init__(self, repo: str, files: List[str], projector: str = "", draft: str = "",
                 preset: Optional[Dict[str, Any]] = None) -> None:
        self.id = uuid.uuid4().hex[:10]

        self.preset = {key: value for key, value in (preset or {}).items() if key in PRESET_KEYS}
        self.repo = repo
        self.files = files
        self.projector = projector
        self.draft = draft
        self.total = 0
        self.done = 0
        self.speed = 0.0
        self.state = "queued"
        self.error = ""
        self.error_en = ""
        self.model = ""
        self.started = time.time()
        self.cancel = threading.Event()

    def public(self) -> Dict[str, Any]:
        remaining = max(0, self.total - self.done)
        return {"id": self.id, "repo": self.repo, "files": self.files, "projector": self.projector,
                "draft": self.draft, "preset": self.preset, "total": self.total,
                "done": self.done, "speed": int(self.speed), "state": self.state, "error": self.error,
                "error_en": self.error_en,
                "model": self.model, "eta": int(remaining / self.speed) if self.speed > 1 else None,
                "name": _SHARD_RE.sub(".gguf", self.files[0]).split("/")[-1] if self.files else ""}

_downloads: Dict[str, Download] = {}
_downloads_lock = threading.Lock()

def downloads() -> List[Dict[str, Any]]:
    with _downloads_lock:
        rows = [item.public() for item in _downloads.values()]
    return sorted(rows, key=lambda row: row["state"] != "downloading")

def start_download(repo: str, files: List[str], projector: str = "", draft: str = "",
                   preset: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not _REPO_RE.match(str(repo or "")):
        raise LocalModelError("Некорректное имя репозитория", "Invalid repository name")
    clean = [str(path) for path in files or [] if str(path).lower().endswith(".gguf") and ".." not in str(path)]
    if not clean:
        raise LocalModelError("Не выбран файл модели", "No model file selected")
    wanted = clean + ([projector] if projector else []) + ([draft] if draft else [])
    basenames = [os.path.normcase(Path(path).name) for path in wanted]
    if len(set(basenames)) != len(basenames):
        raise LocalModelError("Выбранные файлы имеют одинаковые имена; загрузите их отдельно",
                              "The selected files have the same names; download them separately")
    if _DRAFT_RE.search(Path(clean[0]).stem):

        raise _draft_error(Path(clean[0]).name)
    if projector and (".." in projector or not projector.lower().endswith(".gguf")):
        raise LocalModelError("Некорректный файл проектора", "Invalid projector file")
    if draft and (".." in draft or not draft.lower().endswith(".gguf")):
        raise LocalModelError("Некорректный draft-файл", "Invalid draft file")
    with _downloads_lock:
        for item in _downloads.values():
            if item.repo == repo and item.files == clean and item.state in ("queued", "downloading"):
                return item.public()
        job = Download(repo, clean, projector, draft, preset)
        _downloads[job.id] = job
    threading.Thread(target=_run_download, args=(job,), name=f"hf-download-{job.id}", daemon=True).start()
    return job.public()

def cancel_download(download_id: str) -> bool:
    with _downloads_lock:
        job = _downloads.get(str(download_id))
    if not job:
        return False
    job.cancel.set()
    return True

def forget_download(download_id: str) -> None:
    with _downloads_lock:
        job = _downloads.get(str(download_id))
        if job and job.state not in ("queued", "downloading"):
            _downloads.pop(job.id, None)

def _repo_folder(repo: str) -> Path:

    name = re.sub(r"[^A-Za-z0-9_.-]+", "__", repo)[:100]
    identity = hashlib.sha256(repo.encode("utf-8")).hexdigest()[:16]
    return models_dir() / f"hf-{name}-{identity}"

def _linked_sha256(response) -> str:
    """SHA-256 файла LFS из HEAD Hugging Face (x-linked-etag стоит на редиректе)."""
    for item in (*getattr(response, "history", []), response):
        value = str(item.headers.get("x-linked-etag") or "").strip().removeprefix("W/").strip('"').lower()
        if re.fullmatch(r"[0-9a-f]{64}", value):
            return value
    return ""

def _sha256(path: Path, job: Download) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            if job.cancel.is_set():
                raise InterruptedError()
            digest.update(chunk)
    return digest.hexdigest()

def _fetch(client, url: str, target: Path, job: Download, headers: Dict[str, str],
           expected_size: int = 0, expected_sha256: str = "", check=None) -> None:
    """Stream one file to ``target`` with resume from a ``.part`` file.

    ``check(path)`` validates the finished file (raises :class:`LocalModelError`); GGUF header by default.
    Round 27: also used for the wake word's Vosk models (``app/wake_word.py``, a zip check and a duck-typed job).
    """
    part = target.with_name(target.name + ".part")

    def discard(message: str, message_en: str) -> None:

        part.unlink(missing_ok=True)
        raise LocalModelError(message, message_en)

    def verify() -> None:
        if job.cancel.is_set():
            raise InterruptedError()
        if expected_sha256 and _sha256(part, job) != expected_sha256:
            discard("Контрольная сумма SHA-256 не совпала: файл повреждён при загрузке и удалён. Повторите загрузку",
                    "The SHA-256 checksum did not match: the file was damaged in transit and deleted. Retry the download")
        try:
            (check or read_gguf)(part)
        except LocalModelError as exc:
            discard(f"Скачанный файл повреждён и удалён ({exc}). Повторите загрузку",
                    f"The downloaded file is damaged and was deleted ({exc.message_en}). Retry the download")
        if job.cancel.is_set():
            raise InterruptedError()
        part.replace(target)

    have = part.stat().st_size if part.exists() else 0
    request_headers = dict(headers)
    if have:
        request_headers["Range"] = f"bytes={have}-"
    if job.cancel.is_set():
        raise InterruptedError()
    with client.stream("GET", url, headers=request_headers) as response:
        if response.status_code in (401, 403):
            raise LocalModelError("Hugging Face не отдал файл: репозиторий не найден, закрыт или нужен токен",
                                  "Hugging Face did not serve the file: the repository is missing, gated or needs a token")
        if response.status_code == 416:
            match = re.fullmatch(r"bytes\s+\*/(\d+)",
                                 response.headers.get("content-range", "").strip(), re.IGNORECASE)
            total = int(match.group(1)) if match else 0
            if not total:

                raise LocalModelError("Hugging Face не сообщил размер файла; повторите загрузку — "
                                      "уже скачанная часть сохранена",
                                      "Hugging Face did not report the file size; retry the download — "
                                      "the part already downloaded is kept")
            if not have or have != total or (expected_size and total != expected_size):
                discard("Размер частично скачанного файла не совпадает с размером модели на сервере; "
                        "он удалён — повторите загрузку",
                        "The size of the partly downloaded file does not match the model on the server; "
                        "it was deleted — retry the download")
            verify()
            return
        if response.status_code not in (200, 206):
            raise LocalModelError(f"Hugging Face ответил HTTP {response.status_code}",
                              f"Hugging Face answered HTTP {response.status_code}")
        length = response.headers.get("content-length", "")
        if length and not length.isdigit():
            raise LocalModelError("Hugging Face вернул некорректный размер файла",
                                  "Hugging Face returned an invalid file size")
        payload_size = int(length) if length else None
        if response.status_code == 206:
            match = re.fullmatch(r"bytes\s+(\d+)-(\d+)/(\d+)",
                                 response.headers.get("content-range", "").strip(), re.IGNORECASE)
            if not match:
                raise LocalModelError("Hugging Face не подтвердил диапазон частичной загрузки",
                                      "Hugging Face did not confirm the range of the resumed download")
            start, end, total = map(int, match.groups())
            if start != have or end < start or end >= total:
                raise LocalModelError("Hugging Face вернул другой диапазон частичной загрузки",
                                      "Hugging Face returned a different range for the resumed download")
            range_size = end - start + 1
            if payload_size is not None and payload_size != range_size:
                raise LocalModelError("Размер ответа не совпадает с диапазоном частичной загрузки",
                                      "The response size does not match the range of the resumed download")
            payload_size = range_size
        else:
            total = payload_size if payload_size is not None else expected_size
        if expected_size and total != expected_size:
            discard("Размер модели на сервере изменился; повторите загрузку",
                    "The model size on the server changed; retry the download")
        if response.status_code == 200 and have:
            job.done -= have
            have = 0
        mode = "ab" if response.status_code == 206 else "wb"
        last_tick, last_done = time.time(), job.done
        received = 0
        with open(part, mode) as stream:
            for chunk in response.iter_bytes(1024 * 1024):
                if job.cancel.is_set():
                    raise InterruptedError()
                stream.write(chunk)
                received += len(chunk)
                job.done += len(chunk)
                now = time.time()
                if now - last_tick >= 1.0:
                    job.speed = (job.done - last_done) / (now - last_tick) * 0.6 + job.speed * 0.4
                    last_tick, last_done = now, job.done
        if payload_size is not None and received != payload_size:
            raise LocalModelError("Hugging Face вернул неполный файл модели",
                                  "Hugging Face returned an incomplete model file")
        if total:
            size = part.stat().st_size
            if size > total:
                discard("Размер скачанного файла не совпадает с размером модели на сервере; он удалён — повторите загрузку",
                        "The downloaded file size does not match the model on the server; it was deleted — "
                        "retry the download")
            if size < total:

                raise LocalModelError("Загрузка оборвалась; нажмите «Повторить» — она продолжится с того же места",
                                      "The download broke off; press “Retry” and it continues from the same place")
    verify()

def _run_download(job: Download) -> None:
    import httpx
    folder = _repo_folder(job.repo)
    folder.mkdir(parents=True, exist_ok=True)
    wanted = list(job.files) + ([job.projector] if job.projector else []) + ([job.draft] if job.draft else [])
    headers = _hf_headers()
    try:
        job.state = "downloading"
        with _client(timeout=30.0) as client:
            sizes, hashes = {}, {}
            for path in wanted:
                head = client.head(f"{HF_BASE}/{job.repo}/resolve/main/{path}", headers=headers)

                if head.status_code in (401, 403):
                    raise LocalModelError("Hugging Face не отдал файл: репозиторий не найден, закрыт или нужен токен",
                                          "Hugging Face did not serve the file: the repository is missing, gated or needs a token")
                if head.status_code >= 400:
                    raise LocalModelError(f"Hugging Face ответил HTTP {head.status_code} для {path}",
                                          f"Hugging Face answered HTTP {head.status_code} for {path}")
                sizes[path] = int(head.headers.get("x-linked-size") or head.headers.get("content-length") or 0)
                hashes[path] = _linked_sha256(head)
            job.total = sum(sizes.values())

            needed = 0
            for path in wanted:
                target = folder / Path(path).name
                if not (target.exists() and target.stat().st_size == sizes[path]):
                    part = target.with_name(target.name + ".part")
                    needed += max(0, sizes[path] - (part.stat().st_size if part.exists() else 0))
            free = shutil.disk_usage(folder).free
            if needed > free:
                def amount(value: int, gb: str = "ГБ", mb: str = "МБ") -> str:
                    return f"{value / 1024 ** 3:.1f} {gb}" if value >= 1024 ** 3 else f"{value / 1024 ** 2:.0f} {mb}"
                raise LocalModelError(f"Недостаточно места на диске: нужно {amount(needed)}, свободно {amount(free)}. "
                                      "Освободите место или смените папку моделей",
                                      f"Not enough disk space: {amount(needed, 'GB', 'MB')} needed, "
                                      f"{amount(free, 'GB', 'MB')} free. Free up space or change the models folder")
            for path in wanted:
                target = folder / Path(path).name
                if target.exists() and (not sizes[path] or target.stat().st_size == sizes[path]):
                    job.done += target.stat().st_size
                    continue
                part = target.with_name(target.name + ".part")
                job.done += part.stat().st_size if part.exists() else 0
                _fetch(client, f"{HF_BASE}/{job.repo}/resolve/main/{path}", target, job, headers,
                       expected_size=sizes[path], expected_sha256=hashes[path])
        first = folder / Path(job.files[0]).name
        projector = str(folder / Path(job.projector).name) if job.projector else ""
        draft = str(folder / Path(job.draft).name) if job.draft else ""
        job.model = register_file(first, repo=job.repo, mmproj=projector, draft=draft)["id"]
        if job.preset:
            try:
                update_model(job.model, {key: value for key, value in job.preset.items() if key in MODEL_DEFAULTS})
            except LocalModelError:
                pass
        job.state = "done"
    except InterruptedError:
        job.state = "cancelled"
    except OSError as exc:
        job.state = "error"
        if exc.errno == errno.ENOSPC:
            job.error = ("Недостаточно места на диске: загрузка остановлена. Освободите место и повторите — "
                         "скачанное продолжится")
            job.error_en = ("Not enough disk space: the download stopped. Free up space and retry — "
                            "it continues where it stopped")
        else:
            job.error = f"Загрузка прервалась: {type(exc).__name__}: {exc}"
            job.error_en = f"The download broke off: {type(exc).__name__}: {exc}"
    except httpx.HTTPError as exc:
        job.state, job.error = "error", f"Загрузка прервалась: {type(exc).__name__}: {exc}"
        job.error_en = f"The download broke off: {type(exc).__name__}: {exc}"
    except Exception as exc:
        job.state = "error"
        job.error, job.error_en = error_texts(exc)

def _describe(slug: str, row: Dict[str, Any], running: Dict[str, Any]) -> Dict[str, Any]:
    path = Path(str(row.get("path") or ""))
    size = int(row.get("size") or 0)
    settings = {key: row.get(key, MODEL_DEFAULTS[key]) for key in MODEL_DEFAULTS}
    server = running.get(slug) or {}
    return {
        "id": slug,
        "public_id": f"{PREFIX}-{slug}",
        "file": path.name,
        "path": str(path),
        "exists": path.is_file(),
        "size": size,
        "repo": str(row.get("repo") or ""),
        "added": row.get("added", 0),
        "meta": row.get("meta") or {},
        "settings": settings,
        "fit": fit(size, int(settings.get("context") or 8192), row.get("meta") or {}),
        "state": server.get("state", "stopped"),
        "port": server.get("port"),
        "last_used": server.get("last_used"),
        "error": server.get("error", ""),
        "error_en": server.get("error_en", "") or server.get("error", ""),
        "copied": bool(row.get("copied")),

        "draft_head": is_draft_meta(row.get("meta") or {}, path.name),
    }

def list_models() -> List[Dict[str, Any]]:
    data = _load_catalog()
    running = pool.states()
    rows = [_describe(slug, row, running) for slug, row in data["models"].items() if isinstance(row, dict)]
    return sorted(rows, key=lambda row: (-(row["added"] or 0)))

def get_model(slug: str) -> Optional[Dict[str, Any]]:
    row = _load_catalog()["models"].get(str(slug or ""))
    return _describe(slug, row, pool.states()) if isinstance(row, dict) else None

def register_file(path: Path, *, repo: str = "", copy: bool = False, mmproj: str = "",
                  name: str = "", draft: str = "") -> Dict[str, Any]:
    """Add a GGUF file (downloaded, copied or linked in place) to the catalogue."""
    source = Path(path).expanduser()
    if not source.is_file():
        raise LocalModelError("Файл не найден", "File not found")
    if source.suffix.lower() != ".gguf":
        raise LocalModelError("Нужен файл модели в формате .gguf", "A model file in .gguf format is required")
    meta = read_gguf(source)
    if meta.get("architecture") == "clip":
        raise LocalModelError("Это файл проектора изображений (mmproj), а не сама модель. Добавьте его в настройках модели.",
                              "This is an image projector file (mmproj), not the model itself. Add it in the model settings.")
    if meta.get("draft"):
        raise _draft_error(source.name)
    target = source
    if copy:
        target = models_dir() / "imported" / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.resolve() != source.resolve():
            base, index = target, 2
            while True:
                try:
                    destination = target.open("xb")
                except FileExistsError:
                    target = base.with_name(f"{base.stem} ({index}){base.suffix}")
                    index += 1
                    continue
                try:
                    with destination, source.open("rb") as handle:
                        shutil.copyfileobj(handle, destination, length=1024 * 1024)
                    shutil.copystat(source, target)
                except BaseException:
                    target.unlink(missing_ok=True)
                    raise
                break
    size = target.stat().st_size
    shard = _SHARD_RE.search(target.name)
    if shard:
        size = sum(item.stat().st_size for item in target.parent.glob(_SHARD_RE.sub("", target.name) + "-*-of-*.gguf"))
    meta["quant"] = meta.get("quant") or quant_from_name(target.name)
    result: Dict[str, Any] = {}

    def change(data: Dict[str, Any]) -> None:
        existing = next((slug for slug, row in data["models"].items()
                         if isinstance(row, dict) and Path(str(row.get("path") or "")) == target), "")
        slug = existing or _unique_slug(slugify(target.name), data["models"])
        row = dict(MODEL_DEFAULTS)
        row.update(data["models"].get(slug) or {})
        trained = int(meta.get("context_length") or 0)
        row.update({"path": str(target), "size": size, "repo": repo or row.get("repo", ""),
                    "meta": meta, "added": row.get("added") or int(time.time()), "copied": bool(copy)})
        if not existing:
            row["context"] = default_context(size, meta)
            row["name"] = name or meta.get("name") or slug
        if mmproj:
            row["mmproj"] = mmproj
        if draft:
            row["draft"] = draft
        data["models"][slug] = row
        result["id"] = slug

    _mutate(change)
    sync_catalog()
    return get_model(result["id"]) or {"id": result["id"]}

def default_context(size: int, meta: Dict[str, Any]) -> int:
    """Context of a newly added model: 16K when it still fits in video memory, else 8K (never above training)."""
    trained = int(meta.get("context_length") or 0) or 8192
    try:
        roomy = trained >= 16384 and fit(size, 16384, meta)["level"] == "gpu"
    except Exception:
        roomy = False
    return 16384 if roomy else min(8192, trained)

def update_model(slug: str, values: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(values, dict):
        raise LocalModelError("Ожидается объект настроек", "A settings object is expected")

    relaunch = []

    def change(data: Dict[str, Any]) -> None:
        row = data["models"].get(slug)
        if not isinstance(row, dict):
            raise LocalModelError("Модель не найдена", "Model not found")
        for key, value in values.items():
            if key not in MODEL_DEFAULTS:
                continue
            clean = _clean_setting(key, value)
            if key in LAUNCH_KEYS and clean != row.get(key, MODEL_DEFAULTS[key]):
                relaunch.append(key)
            row[key] = clean
        data["models"][slug] = row

    _mutate(change)

    if relaunch:
        pool.stop(slug)
    sync_catalog()
    return get_model(slug) or {}

def _clean_setting(key: str, value: Any) -> Any:
    if key == "enabled":
        return bool(value)
    if key in ("name", "system_prompt"):
        return str(value or "")[:8000 if key == "system_prompt" else 120]
    if key == "extra_args":
        text = str(value or "")[:500]
        try:
            shlex.split(text, posix=False)
        except ValueError as exc:
            raise LocalModelError(f"Дополнительные аргументы: {exc}", f"Extra arguments: {exc}") from exc
        return text
    if key == "mmproj":
        text = str(value or "").strip()
        if text and (not Path(text).is_file() or not text.lower().endswith(".gguf")):
            raise LocalModelError("Файл проектора (mmproj .gguf) не найден",
                                  "The projector file (mmproj .gguf) was not found")
        return text
    if key == "draft":
        text = str(value or "").strip()
        if text and (not Path(text).is_file() or not text.lower().endswith(".gguf")):
            raise LocalModelError("Draft-файл (.gguf) не найден", "The draft file (.gguf) was not found")
        return text
    limits = {"context": (256, 1_048_576), "gpu_layers": (-1, 999), "threads": (0, 256), "batch": (0, 16384),
              "idle_minutes": (0, 1440), "max_tokens": (1, 262_144), "top_k": (0, 1000)}
    floats = {"temperature": (0.0, 5.0), "top_p": (0.0, 1.0), "min_p": (0.0, 1.0), "repeat_penalty": (0.5, 3.0)}
    if value in (None, "") and key in ("temperature", "top_p", "top_k", "min_p", "repeat_penalty", "max_tokens"):
        return None
    if key in limits:
        low, high = limits[key]
        try:
            return max(low, min(high, int(value)))
        except (TypeError, ValueError) as exc:
            raise LocalModelError(f"«{key}» должно быть целым числом", f"“{key}” must be a whole number") from exc
    if key in floats:
        low, high = floats[key]
        try:
            return max(low, min(high, float(value)))
        except (TypeError, ValueError) as exc:
            raise LocalModelError(f"«{key}» должно быть числом", f"“{key}” must be a number") from exc
    return value

def remove_model(slug: str, delete_file: bool = False) -> None:
    pool.stop(slug)
    removed: Dict[str, Any] = {}

    def change(data: Dict[str, Any]) -> None:
        row = data["models"].pop(slug, None)
        if not isinstance(row, dict):
            raise LocalModelError("Модель не найдена", "Model not found")
        removed.update(row)

    _mutate(change)
    if delete_file:
        path = Path(str(removed.get("path") or ""))
        inside = models_dir().resolve()

        try:
            path.resolve().relative_to(inside)
            owned = True
        except ValueError:
            owned = False
        if owned and path.is_file():
            shard = _SHARD_RE.search(path.name)
            pattern = _SHARD_RE.sub("", path.name) + "-*-of-*.gguf" if shard else path.name
            for item in list(path.parent.glob(pattern)) + list(path.parent.glob(pattern + ".part")):
                item.unlink()
    sync_catalog()

def settings_public() -> Dict[str, Any]:
    settings = dict(_load_catalog()["settings"])
    token = str(settings.pop("hf_token", "") or "")
    settings["hf_token_set"] = bool(token)
    return settings

def update_settings(values: Dict[str, Any]) -> Dict[str, Any]:
    def change(data: Dict[str, Any]) -> None:
        settings = data["settings"]
        if "backend" in values:
            backend = str(values["backend"])
            settings["backend"] = backend if backend in ("auto", "cpu", "vulkan", "cuda") else "auto"
        if "server_path" in values:
            path = str(values["server_path"] or "").strip()
            if path and not Path(path).is_file():
                raise LocalModelError("Файл llama-server не найден по этому пути",
                                      "llama-server was not found at this path")
            settings["server_path"] = path
        if "hf_token" in values:
            settings["hf_token"] = str(values["hf_token"] or "").strip()[:200]
        if "max_loaded" in values:
            settings["max_loaded"] = max(1, min(8, int(values["max_loaded"] or 1)))
        if "threads" in values:
            settings["threads"] = max(0, min(256, int(values["threads"] or 0)))

    _mutate(change)
    return settings_public()

def sync_catalog() -> None:
    """Mirror installed models into the Proxy model catalogue and the account list."""
    try:
        from .state import ModelEntry, state
    except Exception:
        return
    data = _load_catalog()
    wanted = {slug: row for slug, row in data["models"].items()
              if isinstance(row, dict) and row.get("enabled", True)}
    if wanted and not any(acc.provider == PREFIX for acc in state.accounts.values()):
        account = state.add_account(PREFIX, {}, label="Локальные модели")
        account.note = "Создан автоматически: модели этого аккаунта работают на вашем компьютере."
        state.save_accounts()
    for entry_id, entry in list(state.models.items()):
        if entry.provider == PREFIX and entry.model not in wanted:
            state.remove_model(entry_id)
    for slug, row in wanted.items():
        entry = ModelEntry(PREFIX, PREFIX, slug, source="local", note=str(row.get("name") or slug))
        current = state.models.get(entry.id)
        if current is None or current.note != entry.note:
            state.upsert_model(entry)

_install: Dict[str, Any] = {"state": "idle", "done": 0, "total": 0, "error": "", "error_en": "", "backend": ""}

_JOB_HANDLE: Optional[int] = None

def _kill_with_proxy(process: "subprocess.Popen[bytes]") -> None:
    """Привязывает llama-server к Job Object с KILL_ON_JOB_CLOSE.

    atexit не выполняется при снятии задачи или падении Proxy, и процессы
    моделей оставались держать гигабайты RAM/VRAM и порты. Windows закрывает
    job вместе с последним дескриптором — то есть вместе с Proxy.
    """
    global _JOB_HANDLE
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if _JOB_HANDLE is None:
            kernel32.CreateJobObjectW.restype = wintypes.HANDLE
            job = kernel32.CreateJobObjectW(None, None)
            if not job:
                return

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [(name, ctypes.c_ulonglong) for name in (
                    "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

            class BASIC_LIMIT(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                            ("PerJobUserTimeLimit", ctypes.c_longlong),
                            ("LimitFlags", wintypes.DWORD),
                            ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t),
                            ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t),
                            ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class EXTENDED_LIMIT(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", BASIC_LIMIT),
                            ("IoInfo", IO_COUNTERS),
                            ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            info = EXTENDED_LIMIT()
            info.BasicLimitInformation.LimitFlags = 0x2000
            kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                         ctypes.c_void_p, wintypes.DWORD]
            if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
                kernel32.CloseHandle(job)
                return
            _JOB_HANDLE = job
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject(_JOB_HANDLE, int(process._handle))  # type: ignore[attr-defined]
    except Exception:
        pass

def _extract_runtime_zip(archive_path: Path, staging: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            destination = (staging / member.filename).resolve()
            if staging.resolve() not in destination.parents and destination != staging.resolve():
                raise LocalModelError("Архив llama.cpp содержит недопустимый путь",
                                      "The llama.cpp archive contains an unsafe path")
        archive.extractall(staging)

def server_binary() -> Optional[Path]:
    data = _load_catalog()
    custom = str(data["settings"].get("server_path") or "").strip()
    if custom and Path(custom).is_file():
        return Path(custom)
    folder = str(data["runtime"].get("folder") or "")
    if folder:
        name = "llama-server.exe" if sys.platform == "win32" else "llama-server"
        for candidate in Path(folder).rglob(name):
            return candidate
    return None

def runtime_status() -> Dict[str, Any]:
    data = _load_catalog()
    binary = server_binary()
    return {
        "installed": bool(binary),
        "path": str(binary) if binary else "",
        "tag": str(data["runtime"].get("tag") or ""),
        "backend": str(data["runtime"].get("backend") or ""),
        "custom": bool(str(data["settings"].get("server_path") or "").strip()),
        "install": dict(_install),
        "platform": sys.platform,
    }

def _pick_backend(requested: str) -> str:
    if requested in ("cpu", "vulkan", "cuda"):
        return requested
    gpus = " ".join(gpu["name"] for gpu in hardware().get("gpus") or []).lower()
    if "nvidia" in gpus:
        return "cuda"
    if any(word in gpus for word in ("amd", "radeon", "intel arc", "geforce")):
        return "vulkan"
    return "cpu"

def _release_assets() -> Tuple[str, List[Tuple[str, str]]]:
    """(tag, [(asset name, download url)]) of the newest numbered build.

    The GitHub API is tried first; it is rate limited per IP, so the public
    release pages serve as a fallback. "latest" is not used on purpose: the
    project marks an auxiliary release as latest, not the builds.
    """
    with _client(timeout=30.0) as client:
        try:
            response = client.get(f"https://api.github.com/repos/{GITHUB_REPO}/releases", params={"per_page": "15"},
                                  headers={"Accept": "application/vnd.github+json"})
            if response.status_code == 200:
                for release in response.json():
                    tag = str(release.get("tag_name") or "")
                    assets = [(str(a.get("name")), str(a.get("browser_download_url"))) for a in release.get("assets") or []]
                    if re.fullmatch(r"b\d+", tag) and any("-bin-win-" in name for name, _ in assets):
                        return tag, assets
        except Exception:
            pass
        page = client.get(f"https://github.com/{GITHUB_REPO}/releases")
        tags = re.findall(rf"/{re.escape(GITHUB_REPO)}/releases/tag/(b\d+)", page.text)
        if not tags:
            raise LocalModelError("Не удалось узнать последнюю сборку llama.cpp на GitHub",
                                  "Could not find the latest llama.cpp build on GitHub")
        tag = tags[0]
        listing = client.get(f"https://github.com/{GITHUB_REPO}/releases/expanded_assets/{tag}")
        names = sorted(set(re.findall(rf"/releases/download/{tag}/([^\"'<> ]+)", listing.text)))
        if not names:
            raise LocalModelError("GitHub не отдал список файлов сборки llama.cpp",
                                  "GitHub did not return the file list of the llama.cpp build")
        return tag, [(name, f"https://github.com/{GITHUB_REPO}/releases/download/{tag}/{name}") for name in names]

def _choose_assets(assets: List[Tuple[str, str]], backend: str) -> List[Tuple[str, str]]:
    def version(name: str) -> Tuple[int, ...]:
        match = re.search(r"cuda-(\d+)\.(\d+)", name)
        return (int(match.group(1)), int(match.group(2))) if match else (0, 0)

    wins = [(name, url) for name, url in assets if name.endswith(".zip") and "-x64" in name]
    if backend == "cuda":
        builds = sorted((row for row in wins if re.search(r"^llama-.*-bin-win-cuda-[\d.]+-x64\.zip$", row[0])),
                        key=lambda row: version(row[0]))

        preferred = [row for row in builds if version(row[0])[0] == 12] or builds
        if not preferred:
            raise LocalModelError("В сборке нет варианта для CUDA", "The build has no CUDA variant")
        build = preferred[-1]
        runtime = next((row for row in wins if row[0] == f"cudart-llama-bin-win-cuda-{'.'.join(map(str, version(build[0])))}-x64.zip"), None)
        return [build] + ([runtime] if runtime else [])
    suffix = "-bin-win-vulkan-x64.zip" if backend == "vulkan" else "-bin-win-cpu-x64.zip"
    build = next((row for row in wins if row[0].startswith("llama-") and row[0].endswith(suffix)), None)
    if not build:
        raise LocalModelError(f"В сборке нет варианта «{backend}» для Windows x64",
                          f"The build has no “{backend}” variant for Windows x64")
    return [build]

def install_runtime(backend: str = "auto") -> Dict[str, Any]:
    """Start downloading llama.cpp in the background; progress is in runtime_status()."""
    if sys.platform != "win32":
        raise LocalModelError("Автоустановка движка есть только для Windows. Укажите путь к своему llama-server в настройках.",
                              "Automatic engine setup is Windows-only. Set the path to your own llama-server in the settings.")
    if _install["state"] == "running":
        return runtime_status()
    chosen = _pick_backend(str(backend or "auto"))
    _install.update({"state": "running", "done": 0, "total": 0, "error": "", "error_en": "", "backend": chosen})
    threading.Thread(target=_run_install, args=(chosen,), name="llama-install", daemon=True).start()
    return runtime_status()

def _run_install(backend: str) -> None:
    try:
        tag, assets = _release_assets()
        chosen = _choose_assets(assets, backend)
        target = runtime_dir() / f"{tag}-{backend}"
        staging = runtime_dir() / f".staging-{uuid.uuid4().hex[:6]}"
        staging.mkdir(parents=True, exist_ok=True)
        with _client(timeout=60.0) as client:
            for name, url in chosen:
                with client.stream("GET", url) as response:
                    if response.status_code != 200:
                        raise LocalModelError(f"GitHub ответил HTTP {response.status_code} на {name}",
                                              f"GitHub answered HTTP {response.status_code} for {name}")
                    _install["total"] += int(response.headers.get("content-length") or 0)

                    archive_path = staging.parent / f".download-{uuid.uuid4().hex[:8]}.zip"
                    with archive_path.open("wb") as handle:
                        for chunk in response.iter_bytes(512 * 1024):
                            handle.write(chunk)
                            _install["done"] += len(chunk)
                try:
                    _extract_runtime_zip(archive_path, staging)
                finally:
                    archive_path.unlink(missing_ok=True)
        if not list(staging.rglob("llama-server.exe")):

            raise LocalModelError("В архиве llama.cpp нет llama-server.exe",
                                  "The llama.cpp archive has no llama-server.exe")
        pool.stop_all()
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        staging.rename(target)
        previous = str(_load_catalog()["runtime"].get("folder") or "")

        def change(data: Dict[str, Any]) -> None:
            data["runtime"] = {"tag": tag, "backend": backend, "folder": str(target), "installed": int(time.time())}

        _mutate(change)
        old_folder = _owned_runtime_folder(previous)
        if old_folder and old_folder != target:
            shutil.rmtree(old_folder, ignore_errors=True)
        _install.update({"state": "done"})
    except Exception as exc:
        error, error_en = error_texts(exc)
        _install.update({"state": "error", "error": error, "error_en": error_en})
        for leftover in runtime_dir().glob(".staging-*"):
            shutil.rmtree(leftover, ignore_errors=True)

def _owned_runtime_folder(folder: str) -> Optional[Path]:
    """Only an installed build directly under our runtime directory may be removed."""
    candidate = Path(folder) if folder else Path()
    if not candidate.is_absolute() or candidate.is_symlink():
        return None
    if not re.fullmatch(r"b\d+-(?:cpu|vulkan|cuda)", candidate.name):
        return None
    base = runtime_dir().resolve()
    try:
        if candidate.parent.resolve() == base and candidate.resolve().parent == base:
            return candidate
    except OSError:
        pass
    return None

def remove_runtime() -> None:
    pool.stop_all()
    folder = str(_load_catalog()["runtime"].get("folder") or "")

    def change(data: Dict[str, Any]) -> None:
        data["runtime"] = {}

    _mutate(change)
    owned_folder = _owned_runtime_folder(folder)
    if owned_folder:
        shutil.rmtree(owned_folder, ignore_errors=True)

_EXIT_CODES: Dict[int, Tuple[str, str, str, str]] = {
    0xC0000005: ("нарушение доступа к памяти",
                 "файл не является полной моделью (например, draft/MTP-голова), повреждён, архитектура ещё "
                 "не поддерживается этой сборкой llama.cpp или не хватило видеопамяти. Попробуйте уменьшить "
                 "«Слои на GPU», скачать другой квант или обновить движок.",
                 "memory access violation",
                 "the file is not a complete model (for example a draft/MTP head), it is damaged, this llama.cpp "
                 "build does not support the architecture yet, or video memory ran out. Try fewer "
                 "“GPU layers”, another quant or an engine update."),
    0xC0000409: ("внутренняя ошибка llama.cpp (abort)",
                 "обычно это неподдерживаемая или повреждённая модель, либо несовместимые параметры запуска. "
                 "Подробности — в строках журнала ниже.",
                 "internal llama.cpp error (abort)",
                 "usually an unsupported or damaged model, or incompatible launch settings. "
                 "The log lines below have the details."),
    0xC000001D: ("недопустимая инструкция процессора",
                 "сборка движка требует инструкций, которых нет у этого процессора. Переустановите движок "
                 "с вариантом «CPU» или «Vulkan».",
                 "illegal processor instruction",
                 "the engine build needs instructions this processor lacks. Reinstall the engine "
                 "with the “CPU” or “Vulkan” variant."),
    0xC0000135: ("не найдена библиотека DLL",
                 "повреждена установка движка или нет драйвера видеокарты. Переустановите движок.",
                 "a DLL library was not found",
                 "the engine installation is damaged or the graphics driver is missing. Reinstall the engine."),
    0xC0000142: ("библиотека DLL не смогла инициализироваться",
                 "переустановите движок и обновите драйвер видеокарты.",
                 "a DLL library failed to initialise",
                 "reinstall the engine and update the graphics driver."),
    0xC0000017: ("не хватило памяти",
                 "модель не помещается в память: уменьшите контекст, выберите меньший квант или "
                 "закройте другие программы.",
                 "out of memory",
                 "the model does not fit in memory: lower the context, pick a smaller quant or "
                 "close other programs."),
    0xC00000FD: ("переполнение стека", "попробуйте другую версию движка или другой файл модели.",
                 "stack overflow", "try another engine version or another model file."),
}

def describe_exit(code: Optional[int], log: List[str], english: bool = False) -> str:
    """A readable reason for a llama-server process that exited on start (Russian, or English)."""
    unsigned = (int(code) & 0xFFFFFFFF) if code is not None else None
    known = _EXIT_CODES.get(unsigned) if unsigned is not None else None
    if known and english:
        head = (f"llama-server crashed: {known[2]} (code 0x{unsigned:08X}).\n"
                f"Common causes: {known[3]}")
    elif known:
        head = (f"llama-server аварийно завершился: {known[0]} (код 0x{unsigned:08X}).\n"
                f"Частые причины: {known[1]}")
    else:
        head = f"llama-server exited with code {code}." if english else f"llama-server завершился с кодом {code}."

    lines = [line for line in log if line.strip()]
    errors = [line for line in lines if re.search(r"(?i)(^|\s)E\s|error|failed|unable|cannot|not supported|out of memory", line)]
    picked = (errors[-6:] + [line for line in lines[-6:] if line not in errors[-6:]])[-10:]
    caption = "Last log lines" if english else "Последние строки журнала"
    return (head + (f"\n\n{caption}:\n" + "\n".join(picked) if picked else "")).strip()

class Server:
    def __init__(self, slug: str) -> None:
        self.slug = slug
        self.process: Optional[subprocess.Popen] = None
        self.generation = 0
        self.port = 0
        self.state = "stopped"
        self.error = ""
        self.error_en = ""
        self.last_used = 0.0
        self.log: Deque[str] = deque(maxlen=300)
        self.lock = threading.Lock()

    def public(self) -> Dict[str, Any]:
        return {"state": self.state, "port": self.port, "error": self.error, "error_en": self.error_en,
                "last_used": int(self.last_used) if self.last_used else None}

_fit_support: Dict[str, bool] = {}

def supports_fit(binary: str) -> bool:
    """Does this llama-server fit unset parameters to free memory itself (``--fit``, llama.cpp ≥ b7400)?

    Answered from ``--help`` once per binary (path + modification time); our own numbered build
    tag is the fallback when the probe fails.
    """
    path = Path(str(binary or ""))
    try:
        key = f"{path}|{path.stat().st_mtime_ns}"
    except OSError:
        return False
    if key in _fit_support:
        return _fit_support[key]
    result = False
    try:
        output = subprocess.run([str(path), "--help"], capture_output=True, text=True, timeout=25,
                                creationflags=_NO_WINDOW, cwd=str(path.parent), stdin=subprocess.DEVNULL)
        text = (output.stdout or "") + (output.stderr or "")
        if "--n-gpu-layers" in text or "-ngl" in text:
            result = bool(re.search(r"(^|\s)--fit[\s,]", text))
        else:
            raise OSError("no help text")
    except (OSError, subprocess.SubprocessError):
        match = re.fullmatch(r"b(\d+)", str(_load_catalog()["runtime"].get("tag") or ""))
        result = bool(match and int(match.group(1)) >= 7400 and not _load_catalog()["settings"].get("server_path"))
    _fit_support[key] = result
    return result

def auto_gpu_layers(row: Dict[str, Any]) -> int:
    """Layers to offload when the engine cannot fit them itself: all that fit in ~90 % of VRAM."""
    hw = hardware()
    vram = max((gpu.get("vram") or 0 for gpu in hw.get("gpus") or [] if not gpu.get("integrated")), default=0)
    if vram < 1024 ** 3:
        return 999
    meta = row.get("meta") or {}
    layers = int(meta.get("layers") or 0)
    size = int(row.get("size") or 0)
    context = int(row.get("context") or 8192)
    kv = context * (kv_bytes_per_token(meta) or 128 * 1024)
    budget = vram * 0.9 - 600 * 1024 * 1024 - kv
    if row.get("mmproj"):
        try:
            budget -= Path(str(row["mmproj"])).stat().st_size
        except OSError:
            pass
    if not layers or not size or budget >= size:
        return 999
    return max(0, int(layers * budget / size))

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]

class Pool:
    """The llama-server processes. Thread-safe; blocking calls go to threads."""

    def __init__(self) -> None:
        self._servers: Dict[str, Server] = {}
        self._lock = threading.RLock()
        self._reaper: Optional[threading.Thread] = None

        self.command_override: Optional[List[str]] = None

    def _server(self, slug: str) -> Server:
        with self._lock:
            server = self._servers.get(slug)
            if server is None:
                server = self._servers[slug] = Server(slug)
            return server

    def states(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {slug: server.public() for slug, server in self._servers.items()}

    def log(self, slug: str) -> List[str]:
        with self._lock:
            server = self._servers.get(slug)
            return list(server.log) if server else []

    def touch(self, slug: str) -> None:
        with self._lock:
            server = self._servers.get(slug)
            if server:
                server.last_used = time.time()

    def command(self, row: Dict[str, Any], port: int, alias: str) -> List[str]:
        settings = _load_catalog()["settings"]
        if self.command_override is not None:
            prefix = list(self.command_override)
        else:
            binary = server_binary()
            if not binary:
                raise LocalModelError("Движок llama.cpp не установлен. Откройте «Локальные модели» и нажмите «Установить движок».",
                                      "The llama.cpp engine is not installed. Open “Local models” and press “Install engine”.")
            prefix = [str(binary)]
        backend = str(_load_catalog()["runtime"].get("backend") or "")
        gpu_layers = int(row.get("gpu_layers", -1))
        layer_args: List[str] = []
        if gpu_layers >= 0:
            layer_args = ["-ngl", str(gpu_layers)]
        elif backend == "cpu" and not settings.get("server_path"):
            layer_args = ["-ngl", "0"]
            gpu_layers = 0
        elif self.command_override is None and supports_fit(prefix[0]):

            gpu_layers = -1
        else:
            gpu_layers = auto_gpu_layers(row)
            layer_args = ["-ngl", str(gpu_layers)]
        args = prefix + ["-m", str(row["path"]), "--host", "127.0.0.1", "--port", str(port),
                         "-c", str(int(row.get("context") or 8192))] + layer_args + [
                         "--alias", alias, "--jinja"]
        threads = int(row.get("threads") or settings.get("threads") or 0)
        if threads:
            args += ["-t", str(threads)]
        if int(row.get("batch") or 0):
            args += ["-b", str(int(row["batch"]))]
        if row.get("mmproj"):
            args += ["--mmproj", str(row["mmproj"])]
        if row.get("draft") and Path(str(row["draft"])).is_file():

            args += ["--model-draft", str(row["draft"])] + (["-ngld", str(gpu_layers)] if gpu_layers >= 0 else [])
        if str(row.get("extra_args") or "").strip():

            args += [token[1:-1] if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'" else token
                     for token in shlex.split(str(row["extra_args"]), posix=False)]
        return args

    def ensure(self, slug: str, timeout: float = 600.0) -> int:
        """Start the model's server if needed and wait until it serves requests."""
        row = _load_catalog()["models"].get(slug)
        if not isinstance(row, dict):
            raise LocalModelError("Локальная модель не найдена", "Local model not found")
        model_path = Path(str(row.get("path") or ""))
        if not model_path.is_file():
            raise LocalModelError(f"Файл модели пропал с диска: {row.get('path')}",
                                  f"The model file is gone from the disk: {row.get('path')}")
        if is_draft_meta(row.get("meta") or {}, model_path.name):
            raise _draft_error(model_path.name)
        server = self._server(slug)
        with server.lock:
            with self._lock:
                if server.state == "ready" and server.process and server.process.poll() is None:
                    server.last_used = time.time()
                    return server.port

                self._make_room(slug)
                server.generation += 1
                generation = server.generation
                port = server.port = _free_port()
                server.log.clear()
                server.error = server.error_en = ""
                server.state = "starting"
            try:
                args = self.command(row, port, f"{PREFIX}-{slug}")
                process = subprocess.Popen(
                    args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    creationflags=_NO_WINDOW, cwd=str(Path(args[0]).parent) if self.command_override is None else None)
            except (OSError, LocalModelError) as exc:
                with self._lock:
                    if server.generation != generation:
                        raise LocalModelError("Загрузка модели отменена", "Loading the model was cancelled") from exc
                    reason, reason_en = (exc.message, exc.message_en) if isinstance(exc, LocalModelError) \
                        else (str(exc), str(exc))
                    server.state, server.error = "error", f"Не удалось запустить llama-server: {reason}"
                    server.error_en = f"Could not start llama-server: {reason_en}"
                    error, error_en = server.error, server.error_en
                raise LocalModelError(error, error_en) from exc
            with self._lock:
                cancelled = server.generation != generation
                if not cancelled:
                    server.process = process
            if cancelled:
                self._terminate(process)

                try:
                    process.stdout.close()
                except OSError:
                    pass
                raise LocalModelError("Загрузка модели отменена", "Loading the model was cancelled")
            _kill_with_proxy(process)
            threading.Thread(target=self._pump, args=(server, process), daemon=True).start()

            def check_current() -> None:

                if server.generation != generation or server.process is not process:
                    raise LocalModelError("Загрузка модели отменена", "Loading the model was cancelled")

            deadline = time.time() + timeout
            import httpx
            while time.time() < deadline:
                with self._lock:
                    check_current()
                if process.poll() is not None:
                    time.sleep(0.2)
                    with self._lock:
                        check_current()
                        server.state = "error"
                        server.error = describe_exit(process.returncode, list(server.log))
                        server.error_en = describe_exit(process.returncode, list(server.log), english=True)
                        error, error_en = server.error, server.error_en
                    raise LocalModelError(error, error_en)
                try:

                    response = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2.0,
                                         trust_env=False)
                    if response.status_code == 200:
                        with self._lock:
                            check_current()
                            if process.poll() is not None:
                                continue
                            server.state = "ready"
                            server.last_used = time.time()
                        self._start_reaper()
                        return port
                except httpx.HTTPError:
                    pass
                time.sleep(0.4)
            with self._lock:
                check_current()
                server.process = None
                server.generation += 1
                server.state, server.error = "error", "Модель не успела загрузиться за отведённое время"
                server.error_en = "The model did not load in the allotted time"
                error, error_en = server.error, server.error_en
            self._terminate(process)
            raise LocalModelError(error, error_en)

    def _pump(self, server: Server, process: subprocess.Popen) -> None:
        try:
            for raw in iter(process.stdout.readline, b""):
                server.log.append(raw.decode("utf-8", errors="replace").rstrip())
        except (OSError, ValueError):
            pass
        finally:
            try:
                process.stdout.close()
            except OSError:
                pass

    def _make_room(self, keep: str) -> None:
        limit = int(_load_catalog()["settings"].get("max_loaded") or 1)
        with self._lock:
            alive = [server for slug, server in self._servers.items()
                     if slug != keep and server.state in ("ready", "starting")]
        alive.sort(key=lambda server: server.last_used)
        while len(alive) >= limit:
            self._kill(alive.pop(0))

    def _kill(self, server: Server) -> None:
        with self._lock:
            process = server.process
            server.process = None
            server.generation += 1
            server.state = "stopped"
        self._terminate(process)

    @staticmethod
    def _terminate(process: Optional[subprocess.Popen]) -> None:
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()

    def stop(self, slug: str) -> None:
        with self._lock:
            server = self._servers.get(slug)
        if server:
            self._kill(server)

    def stop_all(self) -> None:
        with self._lock:
            servers = list(self._servers.values())
        for server in servers:
            self._kill(server)

    def _start_reaper(self) -> None:
        if self._reaper and self._reaper.is_alive():
            return
        self._reaper = threading.Thread(target=self._reap_loop, name="llama-idle", daemon=True)
        self._reaper.start()

    def _reap_loop(self) -> None:
        while True:
            time.sleep(30)
            models = _load_catalog()["models"]
            with self._lock:
                servers = list(self._servers.values())
            active = False
            for server in servers:
                if server.state != "ready":
                    continue
                active = True
                idle = int((models.get(server.slug) or {}).get("idle_minutes", 10) or 0)
                if idle and time.time() - server.last_used > idle * 60:
                    self._kill(server)
            if not active:
                return

pool = Pool()

atexit.register(pool.stop_all)

def status() -> Dict[str, Any]:
    return {"runtime": runtime_status(), "hardware": hardware(), "settings": settings_public(),
            "models": list_models(), "downloads": downloads()}

KNOWN_SERVERS = (
    ("ollama", "Ollama", "http://127.0.0.1:11434/v1"),
    ("lmstudio", "LM Studio", "http://127.0.0.1:1234/v1"),
    ("llamafile", "llamafile", "http://127.0.0.1:8080/v1"),
    ("jan", "Jan", "http://127.0.0.1:1337/v1"),

    ("koboldcpp", "KoboldCpp", "http://127.0.0.1:5001/v1"),
    ("tgwui", "text-generation-webui", "http://127.0.0.1:5000/v1"),
    ("vllm", "vLLM", "http://127.0.0.1:8000/v1"),
)

def scan_servers() -> List[Dict[str, Any]]:
    """OpenAI-compatible model servers already running on this computer (probed in parallel)."""
    import httpx
    from concurrent.futures import ThreadPoolExecutor

    own_ports = {server.get("port") for server in pool.states().values() if server.get("port")}

    def probe(entry: Tuple[str, str, str]) -> Optional[Dict[str, Any]]:
        key, title, base = entry
        if int(base.rsplit(":", 1)[1].split("/")[0]) in own_ports:
            return None
        try:
            response = httpx.get(f"{base}/models", timeout=1.5, trust_env=False)
            if response.status_code != 200:
                return None
            data = response.json()
            rows = data.get("data") if isinstance(data, dict) else None
            if not isinstance(rows, list):
                return None
            models = [str(row.get("id")) for row in rows if isinstance(row, dict) and row.get("id")]
            return {"id": key, "title": title, "base_url": base, "models": models[:200]}
        except (httpx.HTTPError, ValueError):
            return None

    with ThreadPoolExecutor(max_workers=len(KNOWN_SERVERS)) as executor:
        return [row for row in executor.map(probe, KNOWN_SERVERS) if row]
