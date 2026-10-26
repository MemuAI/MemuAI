"""Поиск моделей Genspark: живой /api/models_config + скан Nuxt-бандла чата."""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import os
import re
import time
from typing import Any, Dict

from ...config import DATA_DIR
from .client import GensparkError

SCAN_CACHE_PATH = os.path.join(DATA_DIR, "discovery_cache.json")

_VENDOR_RE = re.compile(
    r"^(gpt-|o[3-9][a-z0-9.-]*$|claude-|gemini-|grok-|kimi-|glm-|deep-seek|deepseek-|qwen-|llama-|moa-)"
)
_MODEL_ID_RE = re.compile(r'"([a-zA-Z][a-zA-Z0-9._/-]{1,60})"')
_CHUNK_RE = re.compile(r'(?:src|href)="([^"]*?/_nuxt/[A-Za-z0-9._-]+\.js)"')
_IMPORT_RE = re.compile(r'["\'](?:\./)?([A-Za-z0-9._-]+\.js)["\']')
_CODE_RE = re.compile(r"[0-9]")
_JUNK_HINTS = ("node_modules", "woff", "svg", "png", "jpg", "css", "http", "/", "@", " ")

def _load_scan_cache() -> Dict[str, Any]:
    try:
        with open(SCAN_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_scan_cache(cache: Dict[str, Any]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SCAN_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)

def _is_plausible_model_id(s: str) -> bool:
    if not _VENDOR_RE.match(s):
        return False
    if not _CODE_RE.search(s):
        return False
    if any(h in s for h in _JUNK_HINTS):
        return False

    if "fable-5" in s.lower().replace("_", "-"):
        return False
    return len(s) <= 48

def _chunk_urls(html: str, base: str) -> list:
    urls = set()
    for m in _CHUNK_RE.findall(html):
        urls.add(m if m.startswith("http") else base.rstrip("/") + m)
    return sorted(urls)

def parse_models_config(data: Dict[str, Any]) -> Dict[str, list]:
    """{раздел: [имена]} из /api/models_config."""
    out: Dict[str, list] = {}
    for section, kind in (("image_models", "image"), ("audio_models", "audio"), ("video_models", "video")):
        names = []
        for it in data.get(section) or []:
            if isinstance(it, dict):
                name = (it.get("name") or it.get("model") or it.get("model_id")
                        or it.get("id") or it.get("value"))
                if name and name != "auto":
                    names.append(name)
        if names:
            out[kind] = names
    return out

async def discover_chat_models(client, cookie: str, proxy: str = "",
                               max_chunks: int = 400, parallel: int = 10) -> Dict[str, Any]:
    """Скан Nuxt-бандла страницы чата → {added, confirmed, chunks, errors, ids}.

    added/confirmed считаются вызывающим кодом; здесь только поиск id.
    """
    if not cookie:
        raise GensparkError("Нужна рабочая кука для сканирования бандла")
    cache = _load_scan_cache()
    scanned: Dict[str, float] = cache.get("chunks", {})
    base = client.api_base

    html = await client.fetch_text(base + "/agents?type=ai_chat", proxy=proxy, timeout=30)
    chunk_urls = _chunk_urls(html, base)
    if not chunk_urls:
        html_home = await client.fetch_text(base + "/", proxy=proxy, timeout=30)
        chunk_urls = _chunk_urls(html_home, base)

    to_download = [u for u in chunk_urls if scanned.get(os.path.basename(u), 0) < time.time() - 86400][:max_chunks]

    found: Dict[str, str] = {}
    sem = asyncio.Semaphore(parallel)
    errors = 0

    async def fetch_and_scan(url: str) -> None:
        nonlocal errors
        try:
            async with sem:
                text = await client.fetch_text(url, proxy=proxy, timeout=20)
        except Exception:
            errors += 1
            return
        scanned[os.path.basename(url)] = time.time()
        for raw in _MODEL_ID_RE.findall(text):
            if _is_plausible_model_id(raw):
                found.setdefault(raw, "")
        for rel in _IMPORT_RE.findall(text):
            scanned.setdefault(rel, 0.0)

    if to_download:
        await asyncio.gather(*(fetch_and_scan(u) for u in to_download))

    today = _dt.date.today().isoformat()
    cache["chunks"] = {k: v for k, v in scanned.items() if v}
    cache["last_scan"] = today
    _save_scan_cache(cache)
    return {"added": 0, "confirmed": 0, "chunks": len(to_download),
            "errors": errors, "ids": sorted(found), "date": today}
