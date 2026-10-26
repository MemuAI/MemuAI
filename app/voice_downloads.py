"""Cancellable, byte-counted downloads shared by Whisper and Piper."""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .version import APP_VERSION

REPOSITORIES = {
    **{name: "Systran/faster-whisper-" + name for name in (
        "tiny", "tiny.en", "base", "base.en", "small", "small.en", "medium", "medium.en",
        "large-v1", "large-v2", "large-v3")},
    **{name: "Systran/faster-distil-whisper-" + name.removeprefix("distil-") for name in (
        "distil-small.en", "distil-medium.en", "distil-large-v2", "distil-large-v3")},
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}

def _check(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise asyncio.CancelledError()

def _open(url: str, cancel: threading.Event, state: dict, *, method: str = "GET"):
    _check(cancel)
    response = urllib.request.urlopen(urllib.request.Request(url, headers={
        "User-Agent": f"MemuAI/{APP_VERSION}", "Accept-Encoding": "identity"}, method=method), timeout=15)
    state["_response"] = response
    if cancel.is_set():
        response.close()
        raise asyncio.CancelledError()
    return response

def interrupt(state: dict) -> None:
    """Closing an active socket interrupts a read; never block the API thread."""
    response = state.get("_response")
    if response is not None:
        threading.Thread(target=response.close, daemon=True, name="voice-download-cancel").start()

def manifest(model_id: str, state: dict, cancel: threading.Event) -> list[dict[str, Any]]:
    repo = REPOSITORIES.get(model_id)
    if not repo:
        raise ValueError("Неизвестная локальная модель речи")
    state.update(stage="preparing", progress=None, bytes_downloaded=0, total_bytes=0)
    try:
        with _open(f"https://huggingface.co/api/models/{repo}/revision/main?blobs=true", cancel, state) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
        _check(cancel)
        if len(raw) > 4 * 1024 * 1024:
            raise RuntimeError("Слишком большой каталог модели")
        data = json.loads(raw)
        revision = str(data.get("sha") or "")
        if not __import__("re").fullmatch(r"[a-fA-F0-9]{40,64}", revision):
            raise RuntimeError("Сервер модели не вернул версию файлов")
        result = []
        for item in data.get("siblings", []):
            name = str(item.get("rfilename") or "")
            if name not in ("config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.json", "vocabulary.txt"):
                continue
            lfs = item.get("lfs") if isinstance(item.get("lfs"), dict) else {}
            result.append({"name": name, "size": int(item.get("size") or lfs.get("size") or 0),
                "sha256": str(lfs.get("sha256") or ""),
                "url": f"https://huggingface.co/{repo}/resolve/{revision}/{urllib.parse.quote(name)}?download=true"})
        if not {"config.json", "model.bin"}.issubset({x["name"] for x in result}):
            raise RuntimeError("Сервер модели вернул неполный набор файлов")
        return result
    finally:
        state.pop("_response", None)

def transfer(files: list[dict], directory: Path, state: dict, cancel: threading.Event) -> None:

    for item in files:
        if item.get("size", 0) > 0:
            continue
        try:
            with _open(item["url"], cancel, state, method="HEAD") as response:
                item["size"] = max(0, int(response.headers.get("Content-Length") or 0))
        except Exception:
            _check(cancel)
        finally:
            state.pop("_response", None)
    total = sum(item.get("size", 0) for item in files)
    known_total = all(item.get("size", 0) > 0 for item in files)
    received_all = 0
    state.update(stage="downloading", bytes_downloaded=0, total_bytes=total if known_total else 0,
                 progress=0 if known_total else None)
    try:
        for item in files:
            _check(cancel)
            state["current_file"] = item["name"]
            received = 0
            digest = hashlib.sha256()
            with _open(item["url"], cancel, state) as response, (directory / item["name"]).open("wb") as output:
                expected = int(item.get("size") or response.headers.get("Content-Length") or 0)

                read = getattr(response, "read1", response.read)
                while True:
                    _check(cancel)
                    chunk = read(64 * 1024)
                    if not chunk:
                        break
                    _check(cancel)
                    output.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    state["bytes_downloaded"] = received_all + received
                    if known_total:
                        state["progress"] = min(99, int((received_all + received) * 100 / max(1, total)))
                if expected and expected != received:
                    raise RuntimeError("Загрузка прервалась: файл получен не полностью. Повторите загрузку")
            _check(cancel)
            if item.get("sha256") and digest.hexdigest().lower() != item["sha256"].lower():
                raise RuntimeError("Загруженный файл повреждён. Повторите загрузку")
            received_all += received
        state.update(stage="installing", current_file="")
    finally:
        state.pop("_response", None)
