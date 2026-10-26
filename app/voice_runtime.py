"""Voice input/output helpers for the local MemuAI desktop application.

The optional faster-whisper dependency is imported lazily: MemuAI can still
start, use an OpenAI-compatible speech API, and use Windows voices when the
local transcription runtime is unavailable.
"""
from __future__ import annotations

import asyncio
import base64
import errno
import io
import os
import re
import shutil
import tempfile
import threading
import urllib.parse
import urllib.request
import wave
from pathlib import Path
from typing import Any

import httpx

from .version import APP_VERSION

MAX_AUDIO_BYTES = 25 * 1024 * 1024

STT_MODELS: tuple[dict[str, Any], ...] = (
    {"id": "tiny", "name": "Whisper Tiny", "description": "Fastest transcription for modest hardware", "size": "75 MB", "languages": "99+ languages", "language_codes": ["*"], "accuracy": 2, "speed": 5, "streaming": True},
    {"id": "tiny.en", "name": "Whisper Tiny (English)", "description": "Smallest English-only recognizer with very low latency", "size": "75 MB", "languages": "English", "language_codes": ["en"], "accuracy": 3, "speed": 5, "streaming": True},
    {"id": "base", "name": "Whisper Base", "description": "Fast general-purpose recognition for short notes", "size": "142 MB", "languages": "99+ languages", "language_codes": ["*"], "accuracy": 3, "speed": 5, "streaming": True},
    {"id": "base.en", "name": "Whisper Base (English)", "description": "Fast English dictation for everyday notes", "size": "142 MB", "languages": "English", "language_codes": ["en"], "accuracy": 4, "speed": 5, "streaming": True},
    {"id": "small", "name": "Whisper Small", "description": "The best balance of accuracy, speed and size", "size": "466 MB", "languages": "99+ languages", "language_codes": ["*"], "accuracy": 4, "speed": 4, "recommended": True, "streaming": True},
    {"id": "small.en", "name": "Whisper Small (English)", "description": "Accurate English recognition with a compact footprint", "size": "466 MB", "languages": "English", "language_codes": ["en"], "accuracy": 5, "speed": 4, "streaming": True},
    {"id": "medium", "name": "Whisper Medium", "description": "More accurate on long speech and difficult accents", "size": "1.5 GB", "languages": "99+ languages", "language_codes": ["*"], "accuracy": 5, "speed": 2, "streaming": True},
    {"id": "medium.en", "name": "Whisper Medium (English)", "description": "High-accuracy English transcription for long recordings", "size": "1.5 GB", "languages": "English", "language_codes": ["en"], "accuracy": 5, "speed": 3, "streaming": True},
    {"id": "large-v1", "name": "Whisper Large v1", "description": "Earlier large multilingual model for compatible deployments", "size": "3.1 GB", "languages": "99+ languages", "language_codes": ["*"], "accuracy": 5, "speed": 1, "streaming": True},
    {"id": "large-v2", "name": "Whisper Large v2", "description": "High-accuracy multilingual transcription", "size": "3.1 GB", "languages": "99+ languages", "language_codes": ["*"], "accuracy": 5, "speed": 1, "streaming": True},
    {"id": "large-v3", "name": "Whisper Large v3", "description": "Best multilingual accuracy for demanding recordings", "size": "3.1 GB", "languages": "99+ languages", "language_codes": ["*"], "accuracy": 5, "speed": 1, "streaming": True},
    {"id": "large-v3-turbo", "name": "Whisper Large v3 Turbo", "description": "Large-model accuracy with accelerated decoding", "size": "1.6 GB", "languages": "99+ languages", "language_codes": ["*"], "accuracy": 5, "speed": 3, "recommended": True, "streaming": True},
    {"id": "distil-small.en", "name": "Distil-Whisper Small (English)", "description": "Compact English transcription with low latency", "size": "756 MB", "languages": "English", "language_codes": ["en"], "accuracy": 4, "speed": 5, "streaming": True},
    {"id": "distil-medium.en", "name": "Distil-Whisper Medium (English)", "description": "Accurate English recognition with faster inference", "size": "1.5 GB", "languages": "English", "language_codes": ["en"], "accuracy": 5, "speed": 4, "streaming": True},
    {"id": "distil-large-v3", "name": "Distil-Whisper Large v3 (English)", "description": "Fast English model for long recordings", "description_ru": "Быстрая английская модель для длинных записей", "size": "1.5 GB", "languages": "English", "language_codes": ["en"], "accuracy": 5, "speed": 4, "streaming": True},
    {"id": "distil-large-v2", "name": "Distil-Whisper Large v2 (English)", "description": "Efficient English transcription with strong accuracy", "description_ru": "Быстрое и точное распознавание английской речи", "size": "1.5 GB", "languages": "English", "language_codes": ["en"], "accuracy": 5, "speed": 4, "streaming": True},
)

_PIPER_IDS = (
    ("en_US-amy-low", "English · US", "Fast clear American English", "60 MB"),
    ("en_US-lessac-medium", "English · US", "Natural general-purpose English", "75 MB"),
    ("en_US-lessac-high", "English · US", "High-quality American English", "115 MB"),
    ("en_US-ryan-high", "English · US", "Expressive male English voice", "115 MB"),
    ("en_GB-alan-medium", "English · UK", "Natural British English", "75 MB"),
    ("de_DE-thorsten-high", "German", "High-quality German voice", "115 MB"),
    ("de_DE-thorsten-medium", "German", "Balanced German voice", "75 MB"),
    ("fr_FR-siwis-medium", "French", "Natural French voice", "75 MB"),
    ("es_ES-davefx-medium", "Spanish", "Natural European Spanish", "75 MB"),
    ("it_IT-riccardo-x_low", "Italian", "Tiny Italian voice", "25 MB"),
    ("pt_BR-faber-medium", "Portuguese · BR", "Brazilian Portuguese", "75 MB"),
    ("ru_RU-denis-medium", "Russian", "Natural Russian male voice", "64 MB"),
    ("ru_RU-irina-medium", "Russian", "Natural Russian female voice", "64 MB"),
    ("ru_RU-ruslan-medium", "Russian", "Expressive Russian voice", "64 MB"),
    ("uk_UA-ukrainian_tts-medium", "Ukrainian", "Ukrainian voice", "75 MB"),
    ("zh_CN-huayan-medium", "Chinese", "Mandarin voice", "75 MB"),
)

LANGUAGES_RU = {
    "99+ languages": "99+ языков", "English": "английский", "English · US": "английский · США",
    "English · UK": "английский · Великобритания", "German": "немецкий", "French": "французский",
    "Spanish": "испанский", "Italian": "итальянский", "Portuguese · BR": "португальский · Бразилия",
    "Russian": "русский", "Ukrainian": "украинский", "Chinese": "китайский",
}
STT_MODELS = tuple({**model, "languages_ru": LANGUAGES_RU.get(model["languages"], model["languages"])}
                   for model in STT_MODELS)
TTS_MODELS: tuple[dict[str, Any], ...] = tuple(
    {"id": voice_id, "name": f"Piper · {voice_id}", "description": description,
     "size": size, "languages": language, "languages_ru": LANGUAGES_RU.get(language, language), "language_codes": [voice_id.split("-", 1)[0].split("_")[0]],
     "accuracy": 4 if "high" in voice_id or "medium" in voice_id else 3,
     "speed": 5 if "low" in voice_id or "x_low" in voice_id else 4,
     "recommended": voice_id in {"ru_RU-denis-medium", "en_US-lessac-medium", "de_DE-thorsten-medium"},
     "streaming": True, "provider": "piper"}
    for voice_id, language, description, size in _PIPER_IDS
)

def validate_api_base_url(value: object) -> str:
    """Validate a user-configured OpenAI-compatible API root."""
    raw = str(value or "").strip().rstrip("/")
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Base URL должен начинаться с http:// или https://")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Логин и пароль нельзя помещать в Base URL")
    if parsed.query or parsed.fragment:
        raise ValueError("Base URL не должен содержать query или fragment")
    return raw

_AUDIO_SUFFIXES = {"audio/webm": ".webm", "video/webm": ".webm", "audio/ogg": ".ogg", "audio/opus": ".ogg",
                   "application/ogg": ".ogg", "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/wave": ".wav",
                   "audio/vnd.wave": ".wav", "audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/mp4": ".m4a",
                   "audio/x-m4a": ".m4a", "audio/m4a": ".m4a", "video/mp4": ".mp4", "audio/flac": ".flac",
                   "audio/x-flac": ".flac", "audio/aac": ".aac", "audio/x-aac": ".aac", "audio/amr": ".amr"}

def audio_suffix(mime: object, default: str) -> str:
    """``audio/ogg; codecs=opus`` → ``.ogg`` (codec parameters and case ignored)."""
    return _AUDIO_SUFFIXES.get(str(mime or "").split(";", 1)[0].strip().lower(), default)

def decode_audio_data(value: object) -> tuple[bytes, str]:
    """Decode a browser MediaRecorder data URL with a strict size boundary."""
    raw = str(value or "")
    match = re.fullmatch(r"data:(audio/[a-z0-9.+-]+)(?:;[^,]*)?;base64,([A-Za-z0-9+/=\s]+)", raw, re.I)
    if not match:
        raise ValueError("Некорректная аудиозапись")
    try:
        payload = base64.b64decode(re.sub(r"\s+", "", match.group(2)), validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("Аудиозапись повреждена") from exc
    if not payload:
        raise ValueError("Аудиозапись пуста")
    if len(payload) > MAX_AUDIO_BYTES:
        raise ValueError("Аудиозапись превышает лимит 25 МБ")
    return payload, match.group(1).lower()

def decode_audio_pcm(source: Any, sampling_rate: int = 16000) -> Any:
    """Audio file/bytes -> float32 mono PCM (numpy) for faster-whisper.

    faster-whisper's own ``decode_audio`` calls ``av.open(..., metadata_errors="ignore")`` — a keyword
    PyAV 19 removed, so with a fresh ``av`` every local transcription failed with
    "open() got an unexpected keyword argument 'metadata_errors'". Decoding here keeps the engine working
    on every PyAV version (same algorithm: resample to s16 mono, skip broken frames)."""
    import av
    import numpy as np
    resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=sampling_rate)
    chunks: list[Any] = []
    handle = io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source
    with av.open(handle, mode="r") as container:
        frames = container.decode(audio=0)
        while True:
            try:
                frame = next(frames)
            except StopIteration:
                break
            except av.error.InvalidDataError:
                continue
            for out in resampler.resample(frame) or []:
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None) or []:
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0

def prepare_tts_text(value: object, limit: int = 20_000) -> str:
    """Turn Markdown, tables and formula-heavy chat output into speakable prose."""
    text = str(value or "")

    prose = re.sub(r"```[\s\S]*?```|`[^`]*`|\$\$[\s\S]*?\$\$", " ", text)
    ru = bool(re.search(r"[а-яё]", prose, re.I)) or not re.search(r"[a-z]", prose, re.I)
    code_note, picture, formula = ((" Фрагмент кода опущен. ", " Иллюстрация: ", " Формула: ") if ru
                                   else (" Code snippet omitted. ", " Illustration: ", " Formula: "))
    text = re.sub(r"```[^\r\n]*\r?\n[\s\S]*?```|```[\s\S]*?```", code_note, text, flags=re.I)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", lambda m: picture + m.group(1) + ". ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$", "", text, flags=re.M)
    text = re.sub(r"^\s*\|(.+)\|\s*$", lambda m: ". ".join(x.strip() for x in m.group(1).split("|") if x.strip()) + ".", text, flags=re.M)
    text = re.sub(r"\$\$([\s\S]*?)\$\$|\\\[([\s\S]*?)\\\]", lambda m: formula + re.sub(r"\\[A-Za-z]+|[{}_^]", " ", m.group(1) or m.group(2) or "") + ". ", text)
    text = re.sub(r"(?<!\\)\$([^$\n]+)\$", lambda m: re.sub(r"\\[A-Za-z]+|[{}_^]", " ", m.group(1)), text)
    text = re.sub(r"^\s{0,3}(#{1,6}|[-*+]\s|\d+[.)]\s|>\s?)", "", text, flags=re.M)
    text = re.sub(r"[*_~]", "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise ValueError("В сообщении нет текста для озвучивания")
    return text[:limit]

class VoiceRuntime:
    def __init__(self, data_dir: str | os.PathLike[str]) -> None:
        self.root = Path(data_dir) / "memuai" / "voice-models"
        self.stt_root = self.root / "stt"
        self.tts_root = self.root / "tts"
        self.stt_root.mkdir(parents=True, exist_ok=True)
        self.tts_root.mkdir(parents=True, exist_ok=True)
        self._model: Any = None
        self._model_key = ""
        self._model_lock = threading.Lock()
        self.downloads: dict[str, dict[str, Any]] = {}
        self._voice_cache: list[dict[str, str]] | None = None
        self._cancel_events: dict[str, threading.Event] = {}
        self.custom_models: dict[str, dict[str, Any]] = {}

    def set_custom_models(self, entries: list[dict[str, Any]] | None) -> None:
        """Register user-supplied model files without copying or executing them."""
        clean: dict[str, dict[str, Any]] = {}
        for item in entries or []:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or "").strip()[:120]
            path = str(item.get("path") or "").strip()
            kind = "tts" if item.get("kind") == "tts" else "stt"
            if not model_id or not path or model_id in {row["id"] for row in STT_MODELS + TTS_MODELS}:
                continue
            try:
                resolved = Path(path).expanduser().resolve()
            except Exception:
                continue
            codes = [str(code)[:12] for code in item.get("language_codes", ["*"])[:30]] if isinstance(item.get("language_codes"), list) else ["*"]
            clean[f"{kind}:{model_id}"] = {
                "id": model_id, "kind": kind, "name": str(item.get("name") or model_id)[:120],
                "path": str(resolved),
                "language_codes": codes, "languages": ", ".join(codes),
                "streaming": bool(item.get("streaming", False)),
                "size": str(item.get("size") or "Custom")[:40], "custom": True,
            }
        self.custom_models = clean

    def set_root(self, value: str | os.PathLike[str]) -> Path:
        candidate = Path(value).expanduser().resolve()
        if getattr(self, "root", None) == candidate and self.stt_root.is_dir() and self.tts_root.is_dir():

            return candidate
        if getattr(self, "_cancel_events", {}) or any(
                row.get("status") == "downloading" for row in getattr(self, "downloads", {}).values()):
            raise RuntimeError("Нельзя менять папку, пока загрузки моделей ещё выполняются. Дождитесь завершения или окончания отмены")
        candidate.mkdir(parents=True, exist_ok=True)
        self.root = candidate
        self.stt_root = candidate / "stt"; self.tts_root = candidate / "tts"
        self.stt_root.mkdir(parents=True, exist_ok=True); self.tts_root.mkdir(parents=True, exist_ok=True)
        self._model = None; self._model_key = ""
        return candidate

    @staticmethod
    def _speech_synthesizer_type():
        """Load the Windows speech API in-process.

        Older builds launched a temporary PowerShell script for every voice
        action.  Apart from the visible console flash, process/script creation
        is a poor fit for antivirus heuristics.  Python.NET is already part of
        the desktop runtime, so use the signed Windows assembly directly.
        """
        if os.name != "nt":
            raise RuntimeError("Локальное озвучивание доступно только в Windows")
        try:
            import clr
            try:
                clr.AddReference("System.Speech")
            except Exception:
                windows = Path(os.environ.get("WINDIR", r"C:\Windows"))
                assemblies = sorted((windows / "Microsoft.NET" / "assembly" / "GAC_MSIL" / "System.Speech").glob(
                    "v*_*/System.Speech.dll"))
                if not assemblies:
                    raise RuntimeError("Компонент Windows System.Speech не найден")
                clr.AddReference(str(assemblies[-1]))
            from System.Speech.Synthesis import SpeechSynthesizer
            return SpeechSynthesizer
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("Не удалось загрузить локальные голоса Windows") from exc

    def _model_dir(self, model_id: str, kind: str = "stt") -> Path:
        allowed = {row["id"] for row in (TTS_MODELS if kind == "tts" else STT_MODELS)}
        custom = self.custom_models.get(f"{kind}:{model_id}")
        if custom:
            target = Path(custom["path"]).expanduser().resolve()
            if kind == "stt" and target.is_file():
                raise ValueError("Кастомная STT-модель должна указывать на папку модели")
            return target
        if model_id not in allowed:
            raise ValueError("Неизвестная локальная модель речи" if kind == "stt" else "Неизвестная TTS-модель")
        base = self.tts_root if kind == "tts" else self.stt_root
        target = (base / model_id).resolve()
        if base.resolve() not in target.parents:
            raise ValueError("Некорректный путь модели")
        return target

    def _is_downloaded(self, model_id: str, kind: str = "stt") -> bool:
        target = self._model_dir(model_id, kind)
        if f"{kind}:{model_id}" in self.custom_models:
            if kind == "stt":
                return target.is_dir() and (target / "config.json").is_file() and any(target.glob("*.bin"))
            if target.is_file() and target.suffix.lower() == ".onnx":
                return target.with_suffix(".onnx.json").is_file()
            return target.is_dir() and any(p.with_suffix(".onnx.json").is_file() for p in target.glob("*.onnx"))
        if kind == "tts":
            return (target / f"{model_id}.onnx").is_file() and (target / f"{model_id}.onnx.json").is_file()
        return (target / "model.bin").is_file() and (target / "config.json").is_file()

    def catalog(self, active_id: str = "") -> list[dict[str, Any]]:
        result = []
        for source in STT_MODELS:
            row = dict(source)
            state = self.downloads.get("stt:" + row["id"], {})
            row.update({"kind": "stt", "downloaded": self._is_downloaded(row["id"]), "active": row["id"] == active_id,
                        "download_status": state.get("status", "idle"), "download_error": state.get("error", ""),
                        "progress": state.get("progress", 0)})
            row.update({key: state.get(key, 0 if key.endswith("bytes") or key == "bytes_downloaded" else "")
                        for key in ("bytes_downloaded", "total_bytes", "current_file", "stage")})
            result.append(row)
        for item in self.custom_models.values():
            if item["kind"] != "stt":
                continue
            row = dict(item)
            row.update({"downloaded": self._is_downloaded(item["id"], "stt"), "active": item["id"] == active_id,
                        "download_status": "idle", "download_error": "", "progress": 100})
            result.append(row)
        return result

    def tts_catalog(self, active_id: str = "") -> list[dict[str, Any]]:
        result = []
        for source in TTS_MODELS:
            row = dict(source); state = self.downloads.get("tts:" + row["id"], {})
            row.update({"kind": "tts", "downloaded": self._is_downloaded(row["id"], "tts"), "active": row["id"] == active_id,
                        "download_status": state.get("status", "idle"), "download_error": state.get("error", ""),
                        "progress": state.get("progress", 0)})
            row.update({key: state.get(key, 0 if key.endswith("bytes") or key == "bytes_downloaded" else "")
                        for key in ("bytes_downloaded", "total_bytes", "current_file", "stage")})
            result.append(row)
        for item in self.custom_models.values():
            if item["kind"] != "tts":
                continue
            row = dict(item)
            row.update({"downloaded": self._is_downloaded(item["id"], "tts"), "active": item["id"] == active_id,
                        "download_status": "idle", "download_error": "", "progress": 100})
            result.append(row)
        return result

    async def start_download(self, model_id: str, kind: str = "stt") -> dict[str, Any]:
        kind = "tts" if kind == "tts" else "stt"
        self._model_dir(model_id, kind)
        key = f"{kind}:{model_id}"
        state = self.downloads.get(key, {})
        if state.get("status") == "downloading":
            return {"status": "downloading", "progress": state.get("progress"), "stage": state.get("stage", "preparing")}
        state = {"status": "downloading", "error": "", "progress": None, "stage": "preparing"}
        self.downloads[key] = state
        cancel = threading.Event(); self._cancel_events[key] = cancel

        async def runner() -> None:
            try:

                if kind == "tts": await asyncio.to_thread(self._download_tts_model, model_id, state, cancel)
                else: await asyncio.to_thread(self._download_local_model, model_id, state, cancel)
                if cancel.is_set():
                    state.update(status="cancelled", progress=0)
                else:
                    state["status"] = "done"
                    state["progress"] = 100
            except asyncio.CancelledError:
                cancel.set()
                from .voice_downloads import interrupt
                interrupt(state)
                state.update(status="cancelled", progress=0)
            except Exception as exc:
                if cancel.is_set():
                    state.update(status="cancelled", progress=0)
                elif isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
                    state.update(status="error", progress=0,
                                 error="Недостаточно места на диске для модели. Освободите место или выберите другую папку моделей")
                else:
                    state.update(status="error", error=str(exc)[:1000])
            finally:

                if self._cancel_events.get(key) is cancel:
                    self._cancel_events.pop(key, None)

        state["task"] = asyncio.create_task(runner())
        return {"status": state["status"]}

    def cancel_download(self, model_id: str, kind: str = "stt") -> bool:
        key = f"{'tts' if kind == 'tts' else 'stt'}:{model_id}"
        event = self._cancel_events.get(key)
        if not event: return False
        event.set()
        state = self.downloads.get(key, {})
        state.update(status="cancelled", progress=0)
        from .voice_downloads import interrupt
        interrupt(state)
        return True

    def _download_local_model(self, model_id: str, state: dict[str, Any] | None = None,
                              cancel: threading.Event | None = None) -> None:
        state = state if state is not None else {"progress": 0}
        cancel = cancel if cancel is not None else threading.Event()
        target = self._model_dir(model_id, "stt")
        if self._is_downloaded(model_id):
            return
        from .voice_downloads import manifest, transfer
        staging = Path(tempfile.mkdtemp(prefix=f".{model_id}-download-", dir=str(self.root)))
        try:
            files = manifest(model_id, state, cancel)
            transfer(files, staging, state, cancel)
            if cancel.is_set(): raise asyncio.CancelledError()
            if not (staging / "model.bin").is_file() or not (staging / "config.json").is_file():
                raise RuntimeError("Сервер модели вернул неполный набор файлов")
            if target.exists():
                shutil.rmtree(target)
            staging.replace(target)
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    def _download_tts_model(self, model_id: str, state: dict[str, Any], cancel: threading.Event) -> None:
        from .voice_downloads import transfer
        target = self._model_dir(model_id, "tts")
        meta = next((row for row in TTS_MODELS if row["id"] == model_id), None)
        if not meta: raise ValueError("Неизвестная TTS-модель")
        parts = model_id.split("-", 2); lang_code = parts[0]; family = lang_code.split("_", 1)[0]
        voice_name = parts[1]; quality = parts[2]
        base = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/{family}/{lang_code}/{voice_name}/{quality}/{model_id}"
        staging = Path(tempfile.mkdtemp(prefix=f".{model_id}-download-", dir=str(self.tts_root)))
        try:
            files = [{"name": f"{model_id}." + extension, "url": base + "." + extension + "?download=true"}
                     for extension in ("onnx", "onnx.json")]
            transfer(files, staging, state, cancel)
            if cancel.is_set(): raise asyncio.CancelledError()
            if target.exists(): shutil.rmtree(target)
            staging.replace(target); state["progress"] = 98
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def delete_model(self, model_id: str, kind: str = "stt") -> None:
        kind = "tts" if kind == "tts" else "stt"; key = f"{kind}:{model_id}"
        if key in self.custom_models:
            raise RuntimeError("Кастомные модели удаляются из библиотеки настроек; исходный файл не трогается")
        state = self.downloads.get(key, {})
        if state.get("status") == "downloading":
            raise RuntimeError("Дождитесь завершения загрузки модели")
        target = self._model_dir(model_id, kind)
        with self._model_lock:
            if kind == "stt" and self._model_key == model_id:
                self._model = None
                self._model_key = ""
            if target.exists():
                shutil.rmtree(target)
        self.downloads.pop(key, None)

    def _load_local_model(self, model_id: str, download: bool = False) -> Any:
        target = self._model_dir(model_id, "stt")
        if not download and not self._is_downloaded(model_id):
            raise FileNotFoundError("Сначала скачайте выбранную модель речи")
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("Локальный движок речи не установлен. Переустановите MemuAI или выберите API.") from exc
        with self._model_lock:
            cache_key = "custom:" + model_id if f"stt:{model_id}" in self.custom_models else model_id
            if self._model is not None and self._model_key == cache_key:
                return self._model
            if f"stt:{model_id}" not in self.custom_models:
                target.mkdir(parents=True, exist_ok=True)

            self._model = WhisperModel(str(target), device="cpu", compute_type="int8",
                                       local_files_only=True)
            self._model_key = cache_key
            return self._model

    @staticmethod
    def detect_language(text: str) -> str:
        value = str(text or "").lower()
        if not value.strip(): return "und"
        if re.search(r"[іїєґ]", value): return "uk"
        if re.search(r"[\u4e00-\u9fff]", value): return "zh"
        if re.search(r"[ぁ-ゖァ-ヺ]", value): return "ja"
        if re.search(r"[가-힣]", value): return "ko"
        if re.search(r"[а-яё]", value): return "ru"
        if re.search(r"[a-z]", value): return "en"
        return "und"

    @staticmethod
    def _supports(model: dict[str, Any], language: str) -> bool:
        codes = model.get("language_codes") or []
        return "*" in codes or not language or language == "und" or language in codes

    def _tts_files(self, model_id: str) -> tuple[Path, Path]:
        target = self._model_dir(model_id, "tts")
        if f"tts:{model_id}" in self.custom_models:
            if target.is_file():
                model = target
            else:
                pairs = sorted(p for p in target.glob("*.onnx")
                               if p.with_suffix(".onnx.json").is_file())
                if len(pairs) != 1:
                    raise ValueError("Выберите конкретный ONNX-файл: в папке должен быть один голос с соответствующим .onnx.json")
                model = pairs[0]
        else:
            model = target / f"{model_id}.onnx"
        config = model.with_suffix(".onnx.json")
        if not model.is_file() or not config.is_file():
            raise FileNotFoundError("Сначала скачайте выбранную TTS-модель целиком")
        return model, config

    def speak_local_tts(self, text: str, model_id: str, language: str = "auto") -> tuple[bytes, str]:
        meta = next((row for row in TTS_MODELS if row["id"] == model_id), None)
        if meta is None:
            meta = self.custom_models.get(f"tts:{model_id}")
        if not meta:
            raise ValueError("Неизвестная TTS-модель")
        detected = self.detect_language(text) if language in ("", "auto") else language
        if not self._supports(meta, detected):
            raise ValueError(f"Модель {model_id} не поддерживает язык {detected}")
        model, config = self._tts_files(model_id)

        from .tts_process import synthesize_isolated
        return synthesize_isolated(text, model, config)

    def transcribe_local(self, audio: bytes, mime: str, model_id: str, language: str = "auto") -> str:
        language = str(language or "auto").strip().lower().split("-", 1)[0]
        meta = next((row for row in STT_MODELS if row["id"] == model_id), None) or self.custom_models.get(f"stt:{model_id}")
        if meta and language not in ("", "auto") and not self._supports(meta, language):
            raise ValueError(f"Модель {model_id} не поддерживает язык {language}. Выберите многоязычную модель Whisper")
        model = self._load_local_model(model_id)
        path = ""
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=audio_suffix(mime, ".audio")) as handle:
                handle.write(audio)
                path = handle.name
            try:
                source: Any = decode_audio_pcm(path)
            except ImportError:
                source = path
            except Exception as exc:
                raise RuntimeError("Не удалось прочитать аудио") from exc
            if getattr(source, "size", 1) == 0:
                raise RuntimeError("Речь не распознана")
            segments, _info = model.transcribe(source, beam_size=5, vad_filter=True,
                                                language=None if language in ("", "auto") else language)
            text = " ".join(str(segment.text).strip() for segment in segments if str(segment.text).strip()).strip()
            if not text:
                raise RuntimeError("Речь не распознана")
            return text
        finally:
            if path:
                Path(path).unlink(missing_ok=True)

    async def transcribe_api(self, audio: bytes, mime: str, *, base_url: str, api_key: str,
                             model: str, language: str = "auto", lang: str = "ru",
                             transport: httpx.AsyncBaseTransport | None = None) -> str:
        from .stt_providers import transcribe
        return await transcribe(audio, mime, base_url=base_url, api_key=api_key, model=model,
                                language=language, lang=lang, transport=transport)

    async def speak_api(self, text: str, *, base_url: str, api_key: str, model: str, voice: str,
                        speed: float = 1.0, lang: str = "ru") -> tuple[bytes, str]:
        """Cloud/HTTP TTS; the service (OpenAI, Gemini, ElevenLabs, Azure or an
        OpenAI-compatible server) is detected from ``base_url``."""
        from .tts_providers import synthesize
        return await synthesize(text, base_url=base_url, api_key=api_key, model=model, voice=voice,
                                speed=speed, lang=lang)

    def windows_voices(self) -> list[dict[str, str]]:
        if self._voice_cache is not None:
            return list(self._voice_cache)
        if os.name != "nt":
            self._voice_cache = []
            return []
        synthesizer = None
        try:
            synthesizer = self._speech_synthesizer_type()()
            self._voice_cache = [
                {"name": str(item.VoiceInfo.Name), "culture": str(item.VoiceInfo.Culture.Name)}
                for item in synthesizer.GetInstalledVoices()
                if bool(item.Enabled)
            ]
        except Exception:
            self._voice_cache = []
        finally:
            if synthesizer is not None:
                synthesizer.Dispose()
        return list(self._voice_cache)

    def speak_windows(self, text: str, voice: str = "", rate: int = 0,
                      volume: int = 100) -> tuple[bytes, str]:
        if os.name != "nt":
            raise RuntimeError("Локальное озвучивание доступно только в Windows")
        voices = {item["name"] for item in self.windows_voices()}
        if voice and voice not in voices:
            raise ValueError("Выбранный голос Windows не найден")
        temp_dir = Path(tempfile.mkdtemp(prefix="memuai-voice-"))
        wav_path = temp_dir / "speech.wav"
        synthesizer = None
        try:
            synthesizer = self._speech_synthesizer_type()()
            if voice:
                synthesizer.SelectVoice(voice)
            synthesizer.Rate = max(-10, min(10, int(rate)))
            synthesizer.Volume = max(0, min(100, int(volume)))
            synthesizer.SetOutputToWaveFile(str(wav_path))
            synthesizer.Speak(text)
            synthesizer.SetOutputToNull()
            if not wav_path.exists() or wav_path.stat().st_size < 44:
                raise RuntimeError("Windows не удалось озвучить сообщение")
            return wav_path.read_bytes(), "audio/wav"
        except (ValueError, RuntimeError):
            raise
        except Exception as exc:
            raise RuntimeError("Windows не удалось озвучить сообщение") from exc
        finally:
            if synthesizer is not None:
                synthesizer.Dispose()
            shutil.rmtree(temp_dir, ignore_errors=True)
